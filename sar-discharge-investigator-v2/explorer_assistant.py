"""Constrained Ollama commands and source-backed replies for Relax Zone."""
from __future__ import annotations

from collections import OrderedDict, deque
import json
import re
import threading
from time import monotonic
import unicodedata

import requests

from explorer_catalogue import MISSIONS, POLARIZATIONS, PROVIDER_LISTED_CHANNELS, iso_date
from explorer_synopsis import dataset_synopsis
from matrix_answers import INTENTS
from nisar_tool.errors import ApiError
from nisar_tool.server import search_parameters


MODEL = "qwen3.5:4b"
OLLAMA_CHAT = "http://127.0.0.1:11434/api/chat"
TOPICS = ("wetlands", "wetland_flood", "wetland_drought", "oil_spill")
MAP_COMMAND = re.compile(r"^(?:(?:can|could|would) you\s+)?(?:please\s+)?(?:show|find|open|select|load|filter|switch|use|list|display|highlight|mark|outline)\b")
SHOW_EXPLANATION = re.compile(r"^(?:(?:can|could|would) you\s+)?(?:please\s+)?show (?:me )?(?:why\b|how (?:to|does|do|can|oil|water|wetlands|radar|nisar)\b|what\b.{0,50}\b(?:means?|stands? for|indicates?|represents?)\b)")
CHANGE_REQUEST = re.compile(r"\b(?:change[ds]?|differences?|brighter|darker)\b")
SERIES_REQUEST = re.compile(r"\b(?:time[ -]series|(?:radar|backscatter) trend|(?:radar|backscatter) (?:line|chart)|(?:rising|falling) line.{0,20}\bchart)\b")
MECHANISM_QUESTION = re.compile(r"\b(?:why|how (?:does|do|can|would|could)|what (?:causes?|makes?))\b")
LOCATION_REFERENCE = re.compile(r"\b(?:near|around|over|at|in)\s+(?!(?:hh|hv|vv|vh|nisar|l-band|s-band|the map|this (?:app|workspace|area|site|view|image|scene)|the (?:same|other) (?:area|image)|january|february|march|april|may|june|july|august|september|october|november|december)\b)[a-z][\w -]{2,}")
KEEP_CURRENT_WETLAND = re.compile(r"\b(?:here|this (?:area|site|scene|image|crop|view)|(?:same|current) (?:area|site|scene|image|crop|view)|in place)\b")
SYNOPSIS_WORDS = re.compile(r"\b(?:synopsis|summary|overview|summari[sz]e|recap)\b")
CURRENT_SELECTION = re.compile(r"\b(?:this|these|selected|current(?:ly)?|active|displayed)\b")
DATASET_WORDS = re.compile(r"\b(?:data(?:[ -]?set)?|imag(?:e|ery)|scene|capture|acquisition|observation|selection|view|site|area|map)s?\b")
SCREENING_WORDS = re.compile(r"\b(?:check(?:s|ing)?|screen(?:s|ing)?|detect(?:s|ing|ion)?|analys[ie]s|analyz(?:e|es|ing)|look(?:s|ing)? for|support(?:s|ed)?)\b")
OIL_OR_WETLANDS = re.compile(
    r"\b(?:oil|slicks?)\b.{0,28}\bor\b.{0,20}\b(?:wetlands?|flood(?:ing)?)\b"
    r"|\b(?:wetlands?|flood(?:ing)?)\b.{0,28}\bor\b.{0,20}\b(?:oil|slicks?)\b"
)
SCHEMA = {
    "type": "object",
    "properties": {
        "topic": {"enum": [*TOPICS, "unknown"]},
        "site_id": {"type": "string"},
        "polarizations": {"type": "array", "items": {"enum": list(POLARIZATIONS)}, "maxItems": 4},
        "mission": {"enum": ["", "nisar"]},
        "start": {"type": "string"}, "end": {"type": "string"},
        "period": {"enum": ["all", "before", "event", "after", "before_and_event"]},
        "question": {"enum": ["", *INTENTS]},
    },
    "required": ["topic", "site_id", "polarizations", "mission", "start", "end", "period"],
    "additionalProperties": False,
}


def normalize_name(value):
    return "".join(c for c in unicodedata.normalize("NFKD", value.lower()) if not unicodedata.combining(c))


def is_map_command(text):
    return bool((MAP_COMMAND.match(text) and not SHOW_EXPLANATION.match(text)) or re.fullmatch(r"wetlands[.?!]*", text))


def stated_topic(text):
    for pattern, topic in (
        (r"\b(?:oil|slicks?|spills?)\b", "oil_spill"),
        (r"\b(?:drought|drying)\b", "wetland_drought"),
        (r"\b(?:flood(?:s|ed|ing)?|inundat\w*)\b", "wetland_flood"),
        (r"\b(?:wetlands?|marsh(?:es)?|swamps?)\b", "wetlands"),
    ):
        if re.search(pattern, text):
            return topic
    return None


def asks_for_dataset_synopsis(text, map_command):
    current = bool(CURRENT_SELECTION.search(text))
    dataset = bool(DATASET_WORDS.search(text))
    if SYNOPSIS_WORDS.search(text):
        return bool(
            (current and dataset)
            or re.fullmatch(r"(?:please )?(?:(?:give|show) me (?:a |the )?)?(?:synopsis|summary|overview|recap)(?: of (?:this|the selection))?[.!?]*", text)
            or re.fullmatch(r"(?:please )?summari[sz]e (?:this|the selection)[.!?]*", text)
        )
    if map_command or not (current or re.search(r"\bhere\b", text)):
        return False
    return bool(
        OIL_OR_WETLANDS.search(text)
        or (SCREENING_WORDS.search(text) and (dataset or stated_topic(text) or re.search(r"\bboth\b", text)))
        or re.match(r"what (?:are (?:you|we) looking for|checks? (?:can|do) (?:you|we) (?:run|perform))\b", text)
        or (dataset and re.search(
            r"\b(?:tell me about|what can (?:you|we|i) do with)\b|\bwhat (?:is|are)\b.{0,45}\b(?:for|showing|about)\b", text
        ))
    )


def ollama_command(message, state, sites):
    prompt = (
        "Interpret a NISAR radar data selection command. This workspace uses NISAR exclusively. Return only the schema fields. "
        "Topics: wetlands (generic), wetland_flood, wetland_drought, oil_spill. "
        "Use unknown for unrelated tasks or a filter adjustment without a topic. "
        "Use only listed site IDs. Use empty strings and empty arrays for absent filters. "
        "Do not infer unmentioned dates, polarization, or mission. Dates use YYYY-MM-DD. "
        "For a generic request like 'show me wetlands' leave site_id empty. "
        "period describes a relative event date only when asked. "
        "For a question asking for interpretation or advice, use question: compare (which scenes can be compared), "
        "change (how the measured radar return changed), time_series (how to read the chart), polarization (which radar channel and why), coverage (availability of actual pixels), "
        "oil (interpreting possible oil), wetlands (interpreting water or flooding), drought (assessing drought), "
        "legend (explaining colors, lines and markers in the current view). "
        "Use empty question for map instructions such as 'show me wetlands', 'find HH', or 'show Curuai'. "
        "Site names: " + json.dumps(sites, ensure_ascii=True) + ". "
        "Current workspace: " + json.dumps(state, ensure_ascii=True)
    )
    return _ollama_json(prompt, message, SCHEMA, read_timeout=45, max_tokens=256)


def ollama_reference(message, choices):
    """Let the local model choose a source passage without authoring evidence."""
    source_schema = {"type": "object", "properties": {"id": {"enum": [item["id"] for item in choices]}},
                     "required": ["id"], "additionalProperties": False}
    prompt = (
        "Choose the most relevant supplied NISAR Algorithm Theoretical Basis Document summary for the question. "
        "Return only its ID. You cannot select a map action or create new facts. "
        "Mission design and validation are separate from what the app currently calculates. "
        "Reference summaries: " + json.dumps(choices, ensure_ascii=True)
    )
    return _ollama_json(prompt, message, source_schema, read_timeout=6, max_tokens=48)["id"]


def _ollama_json(prompt, message, schema, *, read_timeout, max_tokens):
    with requests.Session() as session:
        session.trust_env = False
        try:
            with session.post(OLLAMA_CHAT, json={
                "model": MODEL, "stream": False, "think": False, "format": schema,
                "options": {"temperature": 0, "num_predict": max_tokens, "num_ctx": 4096},
                "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": message}],
            }, stream=True, allow_redirects=False, timeout=(3, read_timeout)) as response:
                if response.status_code != 200:
                    raise ApiError("assistant_unavailable", "Qwen is unavailable. You can continue in Manual mode.", 503)
                received = bytearray()
                for content in response.iter_content(8192):
                    received.extend(content)
                    if len(received) > 32_768:
                        raise ValueError("oversize")
                result = json.loads(received)
                return json.loads(result["message"]["content"])
        except requests.RequestException:
            raise ApiError("assistant_unavailable", "Qwen could not respond. You can continue in Manual mode.", 503) from None
        except (ValueError, KeyError, TypeError):
            raise ApiError("assistant_response_invalid", "Qwen returned an unreadable command. Try narrowing the request.", 502) from None


def matrix_command(message, _state, _sites):
    """Recognize map commands when Qwen is unavailable on the host."""
    text = normalize_name(message)
    topic = stated_topic(text) or "unknown"
    period = "all"
    for pattern, candidate in ((r"\bbefore and (?:at|during)\b", "before_and_event"),
                               (r"\bbefore\b", "before"), (r"\bafter\b", "after"),
                               (r"\b(?:during|event date)\b", "event")):
        if re.search(pattern, text):
            period = candidate
            break
    question = ""
    if not is_map_command(text):
        for pattern, intent in (
            (r"\b(?:legend|colour key|color key|what.*(?:diamond|circle))\b", "legend"),
            (r"\b(?:drought|drying)\b", "drought"),
            (r"\b(?:oil|slicks?|spills?)\b", "oil"),
            (r"\b(?:compare|comparison|matching dates)\b", "compare"),
            (r"\b(?:change[ds]?|brighter|darker)\b", "change"),
            (r"\b(?:polari[sz]ation|co-pol|cross-pol)\b|\b(?:which|why)\b.{0,40}\b(?:channel|hh|hv|vv|vh)\b", "polarization"),
            (r"\b(?:coverage|missing pixels|transparent|valid pixels)\b", "coverage"),
            (r"\b(?:flood(?:s|ed|ing)?|wetlands?|inundat\w*)\b", "wetlands"),
        ):
            if re.search(pattern, text):
                question = intent
                break
    return {"topic": topic, "period": period, "question": question, "site_id": "",
            "polarizations": [], "mission": "", "start": "", "end": ""}


class ExplorerAssistant:
    def __init__(self, catalogue, inference=ollama_command, answers=None, intent_model=None, without_ollama=False,
                 glossary=None, ecosystems=None, settings_guide=None, mission_references=None,
                 reference_inference=ollama_reference):
        self.catalogue = catalogue
        self.inference = inference
        self.answers = answers
        self.intent_model = intent_model
        self.without_ollama = without_ollama
        self.glossary = glossary
        self.ecosystems = ecosystems
        self.settings_guide = settings_guide
        self.mission_references = mission_references
        self.reference_inference = reference_inference
        self.active = threading.Lock()
        self.rate_lock = threading.Lock()
        self.rates = OrderedDict()
        self.model_rates = OrderedDict()
        self.wikipedia_rates = OrderedDict()

    def _validate_request(self, values):
        if not isinstance(values, dict) or set(values) - {"message", "state", "observations"}:
            raise ApiError("invalid_request", "Send a message and current workspace selection.")
        message = values.get("message")
        if not isinstance(message, str) or not 1 <= len(message.strip()) <= 1000:
            raise ApiError("invalid_message", "Enter a command of 1 to 1,000 characters.")
        raw_state = values.get("state", {})
        if not isinstance(raw_state, dict):
            raise ApiError("invalid_state", "Workspace state must be an object.")
        topic = raw_state.get("topic")
        site_id = raw_state.get("site_id")
        mission = raw_state.get("mission")
        state = {"topic": topic if topic in TOPICS else None,
                 "site_id": site_id if isinstance(site_id, str) and site_id in self.catalogue.sites else None,
                 "mission": mission if isinstance(mission, str) and mission in MISSIONS else "all"}
        for key in ("start", "end"):
            value = iso_date(raw_state.get(key), key)
            state[key] = str(value) if value else None
        raw_pol = raw_state.get("polarizations", [])
        state["polarizations"] = list(dict.fromkeys(p for p in raw_pol if p in POLARIZATIONS)) if isinstance(raw_pol, list) else []
        state["bbox"] = search_parameters({"bbox": raw_state["bbox"]})["bbox"] if raw_state.get("bbox") else None
        raw_ids = values.get("observations", [])
        if not isinstance(raw_ids, list) or len(raw_ids) > 30 or any(not isinstance(x, str) or len(x) > 330 for x in raw_ids):
            raise ApiError("invalid_selection", "Pass up to 30 catalogue observation IDs.")
        active = raw_state.get("active_id")
        channel = raw_state.get("channel")
        state["active_id"] = active if isinstance(active, str) and active in raw_ids else None
        state["channel"] = channel if channel in POLARIZATIONS else None
        change = raw_state.get("change_layer_id")
        state["change_layer_id"] = change if isinstance(change, str) and len(change) <= 100 else None
        context = raw_state.get("glossary_context")
        state["glossary_context"] = ([item for item in context[:8] if isinstance(item, str) and len(item) <= 70]
                                      if isinstance(context, list) else [])
        settings_case = raw_state.get("settings_case_id")
        state["settings_case_id"] = (settings_case if self.settings_guide and isinstance(settings_case, str)
                                     and settings_case in self.settings_guide.by_id else None)
        return message.strip(), state, list(dict.fromkeys(raw_ids))

    def _allow_request(self, key, records, limit):
        now = monotonic()
        with self.rate_lock:
            history = records.setdefault(key, deque())
            records.move_to_end(key)
            while len(records) > 512:
                records.popitem(last=False)
            while history and now - history[0] >= 60:
                history.popleft()
            if len(history) >= limit:
                return False
            history.append(now)
            return True

    def respond(self, values, client_key):
        response = self._respond(values, client_key)
        if self.glossary and response.get("answer_model") not in {"glossary", "ecosystems_atbd", "nisar_atbd", "use_case_settings", "dataset_synopsis"}:
            message = response.get("message", "")
            mentions = self.glossary.mentions(message)
            if mentions:
                response["glossary_mentioned_ids"] = mentions
            if response["action"]["type"] in {"answer", "select"}:
                response["message"], used = self.glossary.explain_uses(message)
                if used:
                    response["evidence_ids"] = list(dict.fromkeys([*response.get("evidence_ids", []), *used]))
        return response

    def _respond(self, values, client_key):
        message, state, ids = self._validate_request(values)
        glossary_context = state.pop("glossary_context")
        settings_case = state.pop("settings_case_id")
        normalized = normalize_name(message)
        if not self._allow_request(client_key, self.rates, 24):
            raise ApiError("assistant_rate_limit", "Wait a minute before sending another command. Manual remains available.", 429)
        map_command = is_map_command(normalized)
        if asks_for_dataset_synopsis(normalized, map_command):
            return self._selected_dataset_synopsis(state)
        if self.settings_guide:
            guide_answer = self.settings_guide.request(normalized, settings_case, state["topic"], map_command=map_command)
            if guide_answer:
                return guide_answer
        if self.mission_references and not map_command:
            references = self.mission_references.retrieve(normalized, settings_case)
            if references:
                return self._reference_answer(self.mission_references, message, state["topic"], references, client_key)
        if self.ecosystems and not map_command:
            references = self.ecosystems.retrieve(normalized)
            if references:
                return self._reference_answer(self.ecosystems, message, state["topic"], references, client_key)
        if self.glossary:
            term = self.glossary.online_term(message)
            if term:
                lookup = self.glossary.online_reply(term, allowed=self._allow_request(client_key, self.wikipedia_rates, 4))
                return self._definition_response(state, lookup)
            # Colour and legend questions depend on the current map, while a
            # standalone term should get the saved explanation before inference.
            contextual_legend = re.fullmatch(r"(?:please )?(?:show|explain|read)(?: me)? (?:the |this )?(?:map )?(?:legend|key)[?.!]*", normalized)
            if not map_command and not contextual_legend:
                definition = self.glossary.local_reply(message, glossary_context)
                if definition:
                    return self._definition_response(state, definition)
        if re.search(r"\bsentinel(?:[- ]?[12])?\b", normalized):
            return self._reply("clarify", state["topic"], "Choose a location, date or polarization to search NISAR imagery.")
        named_site = self._named_site(normalized)
        if map_command and named_site and (reference := named_site.get("event_reference")) and re.fullmatch(
                r"(?:please )?(?:show|open|display)(?: me)? (?:the )?(?:strait of hormuz|(?:oil )?slicks? (?:at|in|near) (?:the )?strait of hormuz)[.?!]*", normalized):
            return {"action": {"type": "select", "topic": named_site["topic"], "site_id": named_site["id"],
                               "bbox": named_site["bbox"], "start": reference["date"], "end": reference["date"],
                               "mission": "nisar", "polarizations": [], "selected_ids": []},
                    "message": reference["summary"], "evidence_ids": [reference["evidence_id"]], "model_used": False}
        if self.answers and map_command and not named_site and LOCATION_REFERENCE.search(normalized):
            return self._reply("clarify", state["topic"], "I couldn't match that location to a study area. Open Manual to see the areas with verified imagery.")
        if self.answers and not map_command and re.search(r"\b(?:(?:radar|terrain) shadows?|shadows?.{0,25}(?:hills?|ridges?|slopes?|terrain)|(?:behind|beyond) (?:a |the )?(?:hill|ridge|slope))\b", normalized):
            return self._answer("terrain_shadow", message, state, ids, named_site)
        if self.answers and re.search(r"\b(?:what is (?:sar|radar)\b|what does sar stand for\b|how does radar work\b)", normalized):
            return self._answer("radar_basics", message, state, ids, named_site)
        if self.answers and re.fullmatch(r"(?:please )?(?:show|explain|read)(?: me)? (?:the |this )?(?:map )?(?:legend|key)[?.!]*", normalized):
            return self._answer("legend", message, state, ids, named_site)
        if (self.answers and not map_command
                and re.search(r"\b(?:recognition tests?|model (?:accuracy|results?|performance)|model\b.{0,24}\b(?:work(?:ed|s)?|detect(?:ed|s)?|find|recognize))\b", normalized)):
            return self._answer("recognition", message, state, ids, named_site)
        if self.answers and SERIES_REQUEST.search(normalized) and (not map_command or not named_site):
            return self._answer("time_series", message, state, ids, named_site)
        if self.answers and not map_command and MECHANISM_QUESTION.search(normalized):
            subject = stated_topic(normalized)
            if subject and not re.search(r"\b(?:compare|matching dates|which channel)\b", normalized):
                intent = {"oil_spill": "oil", "wetland_drought": "drought"}.get(subject, "wetlands")
                return self._answer(intent, message, state, ids, named_site)
        if (self.answers and not map_command
                and re.search(r"\b(?:gauges?|water levels?|river levels?|river stage)\b", normalized)):
            return self._answer("water_level", message, state, ids, named_site)
        if self.answers and self.intent_model:
            intent, _confidence = self.intent_model.predict(message)
            if intent in ("change", "compare") and re.search(r"\bdrought\b", normalized):
                intent = "drought"
            if intent == "legend" and re.search(r"\b(?:polarization|polarisation|channel codes?)\b", normalized):
                intent = "polarization"
            if intent in INTENTS and not map_command:
                return self._answer(intent, message, state, ids, named_site)
        if self.without_ollama or not self._allow_request(client_key, self.model_rates, 6):
            return self._without_ollama(message, state, ids)
        if not self.active.acquire(blocking=False):
            return self._without_ollama(message, state, ids)
        try:
            sites = [{"id": s["id"], "name": s["name"], "topic": s["topic"], "aliases": s.get("aliases", [])}
                     for s in self.catalogue.sites.values()]
            command = self.inference(message, state, sites)
        except ApiError as error:
            if not self.answers or error.code not in {"assistant_unavailable", "assistant_response_invalid"}:
                raise
            return self._without_ollama(message, state, ids)
        finally:
            self.active.release()
        return self._apply(command, message, state, ids)

    def _selected_dataset_synopsis(self, state):
        active = state["active_id"]
        observation = next(iter(self.catalogue.known([active])), None) if active else None
        if observation and state["site_id"] and observation.get("site_id") not in (None, state["site_id"]):
            observation = None
        if observation is None:
            reference = self.catalogue.sites.get(state["site_id"], {}).get("event_reference")
            message = reference["summary"] if reference else "Select a dataset on the map and I’ll explain what it can check and which radar data it uses."
            response = self._reply("answer", state["topic"], message)
            if reference:
                response["evidence_ids"] = [reference["evidence_id"]]
        else:
            synopsis = dataset_synopsis(self.catalogue, observation, state["channel"])
            headings = [synopsis[key].rstrip(".") + "." for key in ("dataset", "title") if synopsis.get(key)]
            details = [synopsis[key] for key in ("looking_for", "using", "why", "result") if synopsis.get(key)]
            message = " ".join([*headings, *details])
            response = self._reply("answer", state["topic"], message)
            response["evidence_ids"] = synopsis.get("evidence_ids", [])
        return {**response, "answer_kind": "synopsis", "answer_model": "dataset_synopsis", "model_used": False}

    @staticmethod
    def _definition_response(state, payload):
        return {"action": {"type": "answer", "topic": state["topic"]}, "evidence_ids": [],
                "answer_kind": "definition", "answer_model": "glossary", "model_used": False, **payload}

    def _reference_answer(self, knowledge, message, topic, references, client_key):
        selected = references[0]
        model_used = False
        if (not self.without_ollama and self._allow_request(client_key, self.model_rates, 6)
                and self.active.acquire(blocking=False)):
            try:
                candidate = self.reference_inference(message, knowledge.choices(references))
                if candidate in references:
                    selected, model_used = candidate, True
            except (ApiError, KeyError, TypeError):
                pass
            finally:
                self.active.release()
        return knowledge.answer(selected, topic, model_used=model_used)

    def _without_ollama(self, message, state, ids):
        response = self._apply(matrix_command(message, state, []), message, state, ids)
        response["model_used"] = False
        return response

    def _named_site(self, normalized):
        matches = []
        for site in self.catalogue.sites.values():
            for alias in [site["id"], site["name"], *site.get("aliases", [])]:
                if alias and re.search(r"(?<!\w)" + re.escape(normalize_name(alias)) + r"(?!\w)", normalized):
                    matches.append((len(alias), site))
        match = max(matches, key=lambda item: item[0], default=None)
        return match[1] if match else None

    def _answer(self, intent, message, state, ids, named_site):
        site = named_site or self.catalogue.sites.get(state["site_id"])
        answer, sources = self.answers.answer(intent, message, state, ids, site, named_site=bool(named_site))
        response = {"action": {"type": "answer", "topic": state["topic"]}, "message": answer,
                    "evidence_ids": sources, "answer_model": "nisar_matrix", "answer_kind": intent}
        if intent in {"compare", "change"}:
            layer = self.answers.answer_layer(message, state, ids, site, bool(named_site))
            if layer:
                response["change_layer_id"] = layer["id"]
        return response

    def _apply(self, command, message, state, ids):
        if (not isinstance(command, dict) or set(SCHEMA["required"]) - set(command)
                or set(command) - set(SCHEMA["properties"])):
            raise ApiError("assistant_response_invalid", "Qwen returned an unsupported command. Try a shorter request.", 502)
        topic = command["topic"]
        site_id = command["site_id"]
        mission = command["mission"]
        period = command["period"]
        pol = command["polarizations"]
        if (topic not in (*TOPICS, "unknown") or not isinstance(site_id, str) or mission not in ("", *MISSIONS)
                or period not in SCHEMA["properties"]["period"]["enum"] or not isinstance(pol, list)
                or len(pol) > 4 or any(p not in POLARIZATIONS for p in pol)):
            raise ApiError("assistant_response_invalid", "Qwen returned unsupported filters. Try a shorter request.", 502)
        normalized = normalize_name(message)
        map_command = is_map_command(normalized)
        mission = "nisar" if re.search(r"\bnisar\b", normalized) else ""
        # Only explicit mentions can move the view to a different site. This also
        # makes generic commands stable when the model guesses a well-known site.
        named_site = self._named_site(normalized)
        if (self.answers and command.get("question") in INTENTS and not map_command):
            return self._answer(command["question"], message, state, ids, named_site)
        if named_site:
            site_id = named_site["id"]
        else:
            site_id = state["site_id"]
        site = self.catalogue.sites.get(site_id)
        requested_topic = stated_topic(normalized) if map_command else None
        if map_command:
            if requested_topic:
                topic = requested_topic
            elif named_site:
                topic = named_site["topic"]
        direct_pol = [p for p in POLARIZATIONS if re.search(r"(?<!\w)" + p + r"(?!\w)", message, re.I)]
        if not re.search(r"\b(?:before|after|during|event)\b", normalized):
            period = "all"
        date_mentions = re.findall(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", message)
        wants_change = self.answers and map_command and CHANGE_REQUEST.search(normalized)
        wants_series = self.answers and map_command and SERIES_REQUEST.search(normalized)
        has_adjustment = bool(direct_pol or mission or date_mentions or period != "all" or wants_change or wants_series)
        if topic == "unknown" and not has_adjustment and not named_site:
            return self._reply("clarify", state["topic"], "Ask me to show wetlands, find scenes for an oil spill, or change a data filter.")
        if topic == "unknown":
            topic = named_site["topic"] if named_site else state["topic"] or (site["topic"] if site else None)
        guide_agreement = None
        use_wetland_example = (self.answers and requested_topic == "wetlands" and period == "all"
                               and not (direct_pol or date_mentions or wants_change or wants_series
                                        or KEEP_CURRENT_WETLAND.search(normalized)))
        if use_wetland_example and named_site:
            guide_agreement = self.answers.water_agreement(named_site["id"])
        if self.answers and map_command and not named_site:
            target = "wetlands" if topic and topic.startswith("wetland") else topic
            suggested = self.answers.suggest_site(target) if target else None
            if use_wetland_example and suggested:
                guide_agreement = self.answers.water_agreement(suggested["id"])
            if target and (guide_agreement or not site or site["topic"] != target):
                site = suggested
                site_id = site["id"] if site else None
        same_area = site_id == state["site_id"] and not guide_agreement
        pol = direct_pol or ([guide_agreement["polarization"]] if guide_agreement else state["polarizations"] if same_area else [])
        if topic and topic.startswith("wetland") and (not site or site["topic"] != "wetlands"):
            return self._reply("pick_site", topic, "Choose Curuai for two dates you can compare, or Umm Al Quwain to explore a single coastal wetland scene.")
        if topic == "oil_spill" and (not site or site["topic"] != "oil_spill"):
            return self._reply("pick_site", topic, "Choose an oil study with a measured HH scene. An independent incident record is available for the Umm Al Quwain coast.")
        if not site and not state["bbox"]:
            return self._reply("pick_site" if topic else "clarify", topic, "Choose a site or enter an area on the map to find observations.")
        # The model invented an October interval for an unqualified Tobago
        # prompt during live QA. Dates can only come from explicit input or the
        # verified workspace/preset. A known site name can select its interval.
        try:
            dates = [iso_date(v, "date") for v in date_mentions]
        except ApiError:
            return self._reply("clarify", topic, "Enter the start and end dates as YYYY-MM-DD.")
        start, end = None, None
        if len(dates) > 2:
            return self._reply("clarify", topic, "Which two dates bound the search?")
        if len(dates) == 2:
            start, end = dates
        elif dates:
            if re.search(r"\b(?:to|until|through|ending)\s+\d{4}-\d{2}-\d{2}", normalized):
                end = dates[0]
            elif re.search(r"\b(?:from|starting|since)\s+\d{4}-\d{2}-\d{2}", normalized):
                start = dates[0]
            else:
                start = end = dates[0]
        start = str(start) if start else (state["start"] if same_area else None) or (site or {}).get("start")
        end = str(end) if end else (state["end"] if same_area else None) or (site or {}).get("end")
        if start and end and end < start:
            return self._reply("clarify", topic, "The end date comes before the start date. Which dates should I use?")
        bbox = ((site or {}).get("bbox") if not same_area else state["bbox"]) or (site or {}).get("bbox")
        mission = "nisar"
        known = [row for row in self.catalogue.known(ids)
                 if not site or row.get("site_id") in (None, site_id)] if same_area else []
        seed = [o for o in self.catalogue.seed.values() if site and o.get("site_id") == site_id]
        seed_range = start == (site or {}).get("start") and end == (site or {}).get("end")
        observations = self._filtered(known or (seed if seed_range else []), start, end)
        errors = []
        partial = False
        if not observations and bbox:
            result = self.catalogue.search({"bbox": bbox, "start": start, "end": end, "mission": mission})
            observations = result["observations"]
            partial, errors = result["partial"], result["errors"]
        reference_date = (site or {}).get("reference_acquisition_date") or (site or {}).get("event_date")
        period_rows = self._period(observations, period, reference_date)
        if not pol and topic == "oil_spill":
            pol = ["VV"] if any(self._available(o, "VV") for o in period_rows) else ["HH"]
        preferred = [o for o in period_rows if not pol or any(self._available(o, p) for p in pol)]
        chosen = preferred if preferred else period_rows
        evidence = list(dict.fromkeys(e for e in [((site or {}).get("evidence_id")), *[o.get("evidence_id") for o in chosen]] if e))
        label = site["name"] if site else "the selected area"
        channel = (pol or [state["channel"] if same_area and state["channel"] else "HH"])[0]
        agreement = None
        if (self.answers and map_command and topic == "wetlands" and period == "all" and seed_range and len(chosen) > 1
                and not (date_mentions or direct_pol or wants_change or wants_series)
                and (guide_agreement or not named_site)):
            agreement = self.answers.water_agreement(site_id, channel, chosen)
            if agreement:
                chosen = sorted(chosen, key=lambda row: row["id"] != agreement["observation_id"])
        if self.answers and (preferred or not period_rows):
            reply, sources = self.answers.explain_selection(site, chosen, topic, [channel],
                                                            prefer_change=bool(wants_change), prefer_series=bool(wants_series))
            if preferred and site:
                reply = f"Opened {site['name']}. {reply}"
            evidence = list(dict.fromkeys([*sources, *evidence]))
        elif preferred:
            count = len({o.get("acquisition_id") or o["id"] for o in preferred})
            reply = f"Selected {count} acquisition{'s' if count != 1 else ''} for {label}."
            if pol:
                reply += f" Highlighted {' and '.join(pol)} where the catalogue lists it."
        elif period_rows and pol:
            reply = f"The returned scenes for {label} do not list {' or '.join(pol)}. I kept your channel filter so you can inspect what is available."
        else:
            reply = f"No catalogue observations matched this selection for {label}."
        if period != "all" and reference_date:
            reply += f" The reference observation date is {reference_date} UTC."
        if agreement:
            evidence = list(dict.fromkeys([*evidence, *agreement["evidence_ids"]]))
        if partial:
            reply += " The catalogue response is partial."
        if topic == "wetland_drought" and not self.answers:
            reply += " Drought assessment needs a seasonal water baseline and independent rainfall or water-level evidence."
        action = {"type": "select", "topic": topic, "site_id": site_id, "bbox": bbox, "start": start,
                  "end": end, "mission": mission, "polarizations": pol, "selected_ids": [o["id"] for o in chosen[:20]]}
        if agreement:
            action["recognition_layer_id"] = agreement["id"]
        response = {"action": action, "message": reply, "evidence_ids": evidence, "observations": observations,
                    "partial": partial, "errors": errors}
        if self.answers:
            layer = self.answers.change_layer(preferred, channel)
            if layer:
                response["change_layer_id"] = layer["id"]
                if wants_change:
                    action.update(change_layer_id=layer["id"], polarizations=[channel],
                                  selected_ids=[layer["later_id"], layer["earlier_id"]])
                    response["message"] += " Orange marks stronger return. Olive marks weaker return."
            elif wants_change:
                response["message"] += " A change map needs two measured dates in the same channel. Try Curuai for a verified pair."
        return response

    @staticmethod
    def _available(obs, pol):
        status = obs.get("channels", {}).get(pol, {}).get("status")
        return status == "validated" or status in PROVIDER_LISTED_CHANNELS

    @staticmethod
    def _filtered(observations, start, end):
        rows = []
        for obs in observations:
            acquired = (obs.get("acquired_at") or "")[:10]
            if obs["product"].startswith("NISAR ") and (not start or acquired >= start) and (not end or acquired <= end):
                rows.append(obs)
        return rows

    @staticmethod
    def _period(rows, period, event):
        if period == "all" or not event:
            return rows
        matches = {"before": lambda x: x < event, "after": lambda x: x > event,
                   "event": lambda x: x == event, "before_and_event": lambda x: x <= event}
        return [o for o in rows if matches[period]((o.get("acquired_at") or "")[:10])]

    @staticmethod
    def _reply(kind, topic, message):
        return {"action": {"type": kind, "topic": topic}, "message": message, "evidence_ids": []}

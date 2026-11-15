"""Source-backed terms and explicit, bounded public Wikipedia lookups."""

from __future__ import annotations

from collections import OrderedDict, defaultdict
import json
import re
from threading import BoundedSemaphore, Lock
from time import monotonic
import unicodedata
from urllib.parse import quote, urlencode, urlsplit

from nisar_tool.errors import ApiError


WIKIPEDIAS = ('simple.wikipedia.org', 'en.wikipedia.org')
ID = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*\Z')
POLITE = r'(?:(?:can|could|would) you\s+)?(?:please\s+)?'
WIKI_QUERY = re.compile(POLITE + r'(?:look up|lookup|search for|define|explain|what (?:is|are|does|do))\s+(.+?)\s+(?:mean\s+)?(?:on|in|using|from|according to)\s+(?:the\s+)?wikipedia\Z', re.I)
DEFINITION_QUERY = (
    re.compile(POLITE + r'(?:define|explain|give me the definition of|definition of)\s+(.+)\Z', re.I),
    re.compile(r'(?:what (?:is|are)|what[’\']s)\s+(.+)\Z', re.I),
    re.compile(r'what (?:does|do)\s+(.+?)\s+(?:mean|stand for)\Z', re.I),
    re.compile(r'what (?:do you mean|is meant) by\s+(.+)\Z', re.I),
    re.compile(POLITE + r'(?:tell me (?:the meaning of|what)|show me what)\s+(.+?)(?:\s+(?:means?|stands? for))?\Z', re.I),
    re.compile(r'(.+?)\s+(?:meaning|definition|stands for what)\Z', re.I),
)
CONTEXT = re.compile(r'\s+(?:in (?:SAR|NISAR|this app|this chart|the matrix|the table|the chat|your (?:last )?answer)|on (?:the|this) map|here)\Z', re.I)
ARTICLE = re.compile(r'^(?:the (?:term|word|abbreviation) |the |an? )', re.I)
LIST_DIVIDER = re.compile(r'\s*(?:[,;/+]|\b(?:and|or|versus|vs\.?)\b)\s*', re.I)
DEFINITION_FOLLOWUP = re.compile(POLITE + r'(?:what (?:terms|jargon|technical words) (?:did you use|(?:are |were )?in (?:the|this|your|that) (?:last )?(?:chat|reply|answer|message))|(?:explain|define|list) (?:the |those )?(?:terms|jargon|technical words)(?: (?:you used|in (?:the|this|your|that) (?:last )?(?:chat|reply|answer|message)))?)\Z', re.I)
COMMON_MENTIONS = frozenset({'about', 'area', 'aoi', 'a', 'b', 'band', 'change', 'cutoff', 'date', 'display', 'exposure', 'feature',
                             'flag', 'focus', 'hit', 'image', 'layer', 'look', 'mask', 'model', 'map', 'offset', 'outline', 'pass',
                             'region', 'sample', 'scale', 'scene', 'search', 'source', 'tag', 'test', 'used', 'value', 'water'})


def term_key(value):
    value = value.casefold().translate(str.maketrans({'γ': 'gamma', 'σ': 'sigma', 'β': 'beta', '°': '0'}))
    value = ''.join(letter for letter in unicodedata.normalize('NFKD', value) if not unicodedata.combining(letter))
    normalized = re.sub(r'[^\w]+', ' ', value, flags=re.UNICODE).replace('_', ' ')
    return ' '.join(normalized.split())


def public_term(value):
    """Only short article titles leave the app, with no map or account context."""
    if not isinstance(value, str):
        return None
    value = ' '.join(value.split()).strip(' "\'“”‘’.?!')
    if not 1 <= len(value) <= 80 or len(value.split()) > 10 or not any(ch.isalpha() for ch in value):
        return None
    if any(not (ch.isalnum() or unicodedata.category(ch).startswith('M') or ch in ' -–()’\',.') for ch in value):
        return None
    return value


def wikipedia_search(term):
    return 'https://en.wikipedia.org/w/index.php?' + urlencode({'search': term})


class WikipediaDefinitions:
    def __init__(self, transport):
        self.transport = transport
        self.lock = Lock()
        self.slots = BoundedSemaphore(2)
        self.cache = OrderedDict()

    def _request(self, term):
        params = {'action': 'query', 'prop': 'extracts|pageprops', 'ppprop': 'disambiguation',
                  'exintro': '1', 'explaintext': '1', 'exsentences': '2', 'titles': term,
                  'format': 'json', 'formatversion': '2', 'redirects': '1', 'maxlag': '5'}
        unavailable = False
        for host in WIKIPEDIAS:
            try:
                data = self.transport.get_json(f'https://{host}/w/api.php', params, cap=32_768,
                                               purpose='Explicit Wikipedia definition lookup')
                pages = data.get('query', {}).get('pages', [])
                if 'error' in data or not isinstance(pages, list):
                    raise ValueError('Wikipedia returned an API error')
            except (ApiError, AttributeError, OSError, TypeError, ValueError):
                unavailable = True
                continue
            if len(pages) != 1 or not isinstance(pages[0], dict):
                continue
            page = pages[0]
            title = page.get('title')
            properties = page.get('pageprops', {})
            if (page.get('ns') != 0 or 'missing' in page or not isinstance(title, str) or not title
                    or not isinstance(properties, dict) or 'disambiguation' in properties
                    or not isinstance(page.get('extract'), str)):
                continue
            # Quote a short excerpt, preserving the original wording and credit.
            words = page['extract'].split()
            if not words:
                continue
            excerpt = ' '.join(words[:24])
            if len(words) > 24:
                excerpt = excerpt.rstrip(' ,;:') + '…'
            url = f'https://{host}/wiki/{quote(title.replace(" ", "_"), safe="")}'
            return {'title': title, 'excerpt': excerpt, 'url': url,
                    'edition': 'Simple English Wikipedia' if host == WIKIPEDIAS[0] else 'Wikipedia'}
        if unavailable:
            raise ValueError('Wikipedia lookup unavailable')
        return None

    def lookup(self, term):
        cache_key = term.casefold()
        now = monotonic()
        with self.lock:
            found = self.cache.get(cache_key)
            if found and found[0] > now:
                self.cache.move_to_end(cache_key)
                return found[1], False
        if self.transport is None or not self.slots.acquire(blocking=False):
            return None, True
        try:
            value = self._request(term)
        except (ApiError, AttributeError, OSError, TypeError, ValueError):
            return None, True
        finally:
            self.slots.release()
        with self.lock:
            self.cache[cache_key] = (now + 900, value)
            self.cache.move_to_end(cache_key)
            while len(self.cache) > 64:
                self.cache.popitem(last=False)
        return value, False


class ExplorerGlossary:
    def __init__(self, path, transport=None):
        self.data = json.loads(path.read_text(encoding='utf-8'))
        sources = self.data.get('sources')
        entries = self.data.get('entries')
        if not isinstance(sources, list) or not isinstance(entries, list) or not sources or not entries:
            raise ValueError('The glossary must have terms and sources.')
        self.sources = {}
        canonical = {}
        aliases = defaultdict(dict)
        self.by_id = {}
        self.inline = {}
        for source in sources:
            if not isinstance(source, dict) or not all(isinstance(source.get(key), str) for key in ('id', 'title', 'url')):
                raise ValueError('A glossary source is incomplete.')
            url = urlsplit(source['url'])
            if (not ID.fullmatch(source['id']) or source['id'] in self.sources or not source['title'].strip()
                    or url.scheme != 'https' or not url.hostname or url.username or url.password):
                raise ValueError(f'Invalid glossary source {source["id"]}.')
            self.sources[source['id']] = source
        for entry in entries:
            if not isinstance(entry, dict) or not all(isinstance(entry.get(key), str) and entry[key].strip()
                    for key in ('id', 'term', 'definition', 'example', 'category')):
                raise ValueError('A glossary entry is incomplete.')
            refs, names = entry.get('source_ids'), entry.get('aliases')
            key = term_key(entry['term'])
            if (not ID.fullmatch(entry['id']) or entry['id'] in self.by_id or not key or key in canonical
                    or not isinstance(refs, list) or not refs or not all(isinstance(ref, str) and ref in self.sources for ref in refs)
                    or not isinstance(names, list) or not all(isinstance(name, str) and term_key(name) for name in names)):
                raise ValueError(f'Invalid glossary entry {entry["id"]}.')
            self.by_id[entry['id']] = entry
            canonical[key] = entry
            for name in names:
                aliases[term_key(name)][entry['id']] = entry
            inline = entry.get('inline')
            if inline is not None:
                forms = inline.get('forms') if isinstance(inline, dict) else None
                if (not isinstance(forms, list) or not forms or not all(isinstance(form, str) and form.strip() for form in forms)
                        or not isinstance(inline.get('meaning'), str) or not 1 <= len(inline['meaning']) <= 100
                        or not isinstance(inline.get('already_explained', []), list)
                        or not all(isinstance(phrase, str) and phrase for phrase in inline.get('already_explained', []))):
                    raise ValueError(f'Invalid short meaning for {entry["id"]}.')
                for form in forms:
                    if form.casefold() in self.inline:
                        raise ValueError(f'Duplicate short meaning for {form}.')
                    self.inline[form.casefold()] = entry
        # Canonical names take priority. Shared aliases stay unresolved.
        self.entries = {key: next(iter(matches.values())) for key, matches in aliases.items() if len(matches) == 1}
        self.entries.update(canonical)
        needles = [key for key in self.entries if len(key) >= 2 and key not in COMMON_MENTIONS]
        self.mention_pattern = re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(key) for key in sorted(needles, key=lambda k: (-len(k), k))) + r')(?!\w)')
        self.inline_pattern = (re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(form) for form in sorted(self.inline, key=len, reverse=True)) + r')(?!\w)', re.I)
                               if self.inline else None)
        self.wikipedia = WikipediaDefinitions(transport)

    def _resolve(self, term):
        term = CONTEXT.sub('', term).strip(' "\'“”‘’')
        exact = self.entries.get(term_key(term)) or self.entries.get(term_key(ARTICLE.sub('', term)))
        if exact:
            return [exact]
        parts = LIST_DIVIDER.split(term)
        if not 2 <= len(parts) <= 6:
            return []
        found = [self.entries.get(term_key(part)) for part in parts if part.strip()]
        if not found or not all(found):
            return []
        return list({entry['id']: entry for entry in found}.values())

    @staticmethod
    def online_term(message):
        match = WIKI_QUERY.fullmatch(message.strip().rstrip('?.!'))
        return public_term(match.group(1)) if match else None

    def mentions(self, message):
        return list(dict.fromkeys(self.entries[match.group()]['id']
                                  for match in self.mention_pattern.finditer(term_key(message))))[:8]

    def explain_uses(self, message):
        if not self.inline_pattern:
            return message, []
        pending = []
        emitted = set()
        sources = []
        for match in self.inline_pattern.finditer(message):
            entry = self.inline.get(match.group().casefold())
            if entry is None:
                continue
            identifier = entry['id']
            if identifier in emitted:
                continue
            emitted.add(identifier)
            context = message[max(0, match.start() - 65):match.end() + 65].casefold()
            already_explained = entry['inline'].get('already_explained', ())
            if any(phrase in context for phrase in already_explained) or message[match.end():].lstrip().startswith('('):
                continue
            pending.append((match.end(), entry['inline']['meaning']))
            sources.extend(entry['source_ids'])
            if len(pending) == 2:
                break
        for offset, definition in reversed(pending):
            message = f'{message[:offset]} ({definition}){message[offset:]}'
        return message, list(dict.fromkeys(sources))

    @staticmethod
    def _saved_reply(entries):
        text = '\n\n'.join(f'{entry["term"]}\n{entry["definition"]}' for entry in entries)
        if len(entries) == 1:
            text += f'\n{entries[0]["example"]}'
        ids = [entry['id'] for entry in entries]
        refs = list(dict.fromkeys(ref for entry in entries for ref in entry['source_ids']))
        return {'message': text, 'glossary_term_id': ids[0], 'glossary_term_ids': ids, 'evidence_ids': refs}

    def local_reply(self, message, context=()):
        phrase = message.strip().rstrip('?.!')
        if DEFINITION_FOLLOWUP.fullmatch(phrase):
            entries = [self.by_id[identifier] for identifier in dict.fromkeys(context) if identifier in self.by_id]
            if entries:
                return self._saved_reply(entries)
            return {'message': 'Ask a question about the map first, or give me a term to explain, such as backscatter or HH.'}
        phrase = CONTEXT.sub('', phrase)
        definition_query = next((matched for pattern in DEFINITION_QUERY if (matched := pattern.fullmatch(phrase))), None)
        words = definition_query.group(1) if definition_query else phrase
        entries = self._resolve(words)
        if entries:
            return self._saved_reply(entries)
        if definition_query and re.match(r'^' + POLITE + r'define\s+', phrase, re.I) and public_term(words):
            return {'message': f'I don’t have a saved definition for “{words}”. You can ask me to look it up on Wikipedia.',
                    'glossary_lookup_term': public_term(words)}
        return None

    def online_reply(self, term, allowed=True):
        found, unavailable = self.wikipedia.lookup(term) if allowed else (None, True)
        if found:
            return {'message': f'{found["edition"]} · {found["title"]}\n“{found["excerpt"]}”',
                    'glossary_online_url': found['url'], 'glossary_online_title': found['edition']}
        message = (f'I couldn’t reach Wikipedia for “{term}” just now. You can open its search page to look it up.'
                   if unavailable else f'I couldn’t find one unambiguous Wikipedia article for “{term}”. Try the full term or open Wikipedia’s search results.')
        if not allowed:
            message = 'Please wait a minute before making another online lookup. Saved glossary definitions are still available.'
        return {'message': message, 'glossary_online_url': wikipedia_search(term), 'glossary_online_title': 'Search Wikipedia'}

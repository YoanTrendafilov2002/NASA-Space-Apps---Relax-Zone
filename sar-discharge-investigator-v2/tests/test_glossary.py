"""Deterministic saved definitions, conversational terms and Wikipedia fallback."""

from pathlib import Path
from types import SimpleNamespace
import unittest

from explorer_assistant import ExplorerAssistant
from explorer_glossary import ExplorerGlossary
from nisar_tool.errors import ApiError
from nisar_tool.transport import PublicTransport


DATA = Path(__file__).resolve().parents[1] / 'data' / 'glossary.json'


class Encyclopedia:
    def __init__(self):
        self.calls = []

    def get_json(self, url, params, **details):
        self.calls.append((url, params, details))
        if params['titles'] == 'ambiguous' or url.startswith('https://simple.'):
            return {'query': {'pages': [{'title': 'A term', 'ns': 0, 'pageprops': {'disambiguation': ''},
                                         'extract': 'Which one?'}]}}
        return {'query': {'pages': [{'title': 'Refraction', 'ns': 0,
                                     'extract': 'Refraction is the change in direction of a wave.'}]}}


class GlossaryTests(unittest.TestCase):
    def setUp(self):
        self.transport = Encyclopedia()
        self.glossary = ExplorerGlossary(DATA, self.transport)
        self.assistant = ExplorerAssistant(SimpleNamespace(sites={}), without_ollama=True, glossary=self.glossary)

    def send(self, text, state=None):
        return self.assistant.respond({'message': text, 'state': state or {}}, 'glossary-test')

    def test_saved_terms_are_multi_term_context_aware_and_offline(self):
        result = self.send('What do HH, HV, VH and VV stand for in the table?')
        self.assertEqual(result['answer_model'], 'glossary')
        self.assertEqual(result['glossary_term_ids'], ['hh', 'hv', 'vh', 'vv'])
        self.assertEqual(result['action']['type'], 'answer')
        self.assertTrue(result['evidence_ids'])
        self.assertEqual(self.send('Explain γ⁰')['glossary_term_id'], 'gamma-zero')
        self.assertEqual(self.send('Define map legend')['glossary_term_id'], 'map-legend')
        self.assertEqual(self.transport.calls, [])

    def test_saved_terms_from_the_chat_preserve_exact_context(self):
        mentions = self.glossary.mentions('The time series uses HH and shows backscatter in dB.')
        self.assertIn('backscatter', mentions)
        self.assertIn('decibel', mentions)
        result = self.send('What terms are in the chat?', {'glossary_context': [*mentions, 'unregistered', {}, 123]})
        self.assertEqual(result['glossary_term_ids'], mentions)
        self.assertIn('Time series', result['message'])
        self.assertEqual(self.transport.calls, [])

    def test_reply_expands_jargon_in_plain_text_at_first_use(self):
        text, sources = self.glossary.explain_uses('The HH time series changed by 0.3 dB in HH.')
        self.assertIn('HH (horizontal send and receive)', text)
        self.assertIn('0.3 dB (a scale for comparing echo strength)', text)
        self.assertEqual(text.count('horizontal send and receive'), 1)
        self.assertTrue(sources)
        self.assertEqual(self.glossary.explain_uses('HH transmits and receives horizontally.'), ('HH transmits and receives horizontally.', []))

    def test_online_fallback_requires_an_explicit_lookup_and_sends_only_the_term(self):
        self.assertEqual(self.send('Define refraction')['glossary_lookup_term'], 'refraction')
        self.assertEqual(self.transport.calls, [])
        result = self.send('Look up refraction on Wikipedia')
        self.assertTrue(result['glossary_online_url'].endswith('/wiki/Refraction'))
        self.assertEqual([entry[1]['titles'] for entry in self.transport.calls], ['refraction', 'refraction'])
        self.send('What is refraction on Wikipedia?')
        self.assertEqual(len(self.transport.calls), 2)
        self.send('Look up x@example.com on Wikipedia')
        self.assertEqual(len(self.transport.calls), 2)

    def test_ambiguous_title_links_to_search(self):
        result = self.send('Look up ambiguous on Wikipedia')
        self.assertIn('unambiguous', result['message'])
        self.assertEqual(result['glossary_online_url'], 'https://en.wikipedia.org/w/index.php?search=ambiguous')

    def test_wikipedia_allowlist_is_limited_to_the_fixed_public_api(self):
        self.assertEqual(PublicTransport.validate_url('https://simple.wikipedia.org/w/api.php'),
                         'https://simple.wikipedia.org/w/api.php')
        for value in ('https://en.wikipedia.org/wiki/Refraction', 'https://en.wikipedia.org/w/api.php?titles=Refraction',
                      'https://en.wikipedia.org.example.org/w/api.php'):
            with self.assertRaises(ApiError):
                PublicTransport.validate_url(value)

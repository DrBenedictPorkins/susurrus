"""Showcase profile — exercises the full pipeline (reformatter, continuation,
advisor, term lookup) with generic prompts you can adapt to any meeting.

Copy this file to local/profiles/<your-name>.py and the prompts/demo_meeting_*.md
files to local/prompts/<your-name>_*.md to start your own profile."""
from profiles._base import build_reformatter_steps
from steps.advisor import AdvisorStep
from steps.term_lookup import TermLookupStep
import server

context_words = 800

_doc = None
_advisor = None
_term_lookup = None


def build_steps():
    global _doc, _advisor, _term_lookup
    reformatter, steps = build_reformatter_steps()
    _doc = reformatter.doc
    _advisor = AdvisorStep(
        prompt_name="demo_meeting_advisor",
        backstory_name="demo_meeting_backstory",
        doc=_doc,
        session_file="session_demo_meeting.json",
        log_tag="demo_meeting",
    )
    server.register_instruction_cb(_advisor.set_instruction)
    _term_lookup = TermLookupStep(
        extractor_prompt_name="demo_meeting_terms",
        backstory_name="demo_meeting_backstory",
        doc=_doc,
    )
    return steps + [_advisor, _term_lookup]


def get_doc():
    return _doc


def get_advisor():
    return _advisor


def get_term_lookup():
    return _term_lookup

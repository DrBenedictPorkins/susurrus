from profiles._base import build_reformatter_steps
from steps.monitor import ConversationMonitor

context_words = 800

def build_steps():
    _, steps = build_reformatter_steps()
    return steps + [ConversationMonitor(prompt_name="political_podcast")]

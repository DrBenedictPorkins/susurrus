from steps.monitor import ConversationMonitor

context_words = 500

def build_steps():
    return [ConversationMonitor(prompt_name="banking_call")]

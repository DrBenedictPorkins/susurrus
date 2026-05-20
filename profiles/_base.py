from steps.reformatter import ReformatterStep
from steps.continuation import ContinuationStep


def build_reformatter_steps():
    r = ReformatterStep()
    return r, [r, ContinuationStep(r.doc)]

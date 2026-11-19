"""Transitions explicites ; aucun état terminal ne peut être réouvert."""
TERMINAL = frozenset({'succeeded', 'failed', 'cancelled'})
TRANSITIONS = {
    'queued': {'running', 'cancelled'},
    'running': {'succeeded', 'failed', 'cancel_requested', 'recovering'},
    'recovering': {'succeeded', 'failed', 'cancel_requested'},
    'cancel_requested': {'cancelled'},
}


def transition(job, state):
    if state not in TRANSITIONS.get(job.state, set()):
        raise ValueError('invalid job transition')
    job.state = state

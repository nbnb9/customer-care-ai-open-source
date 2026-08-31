from app.models.tickets import TicketStatus as S
from app.modules.tickets.state import can_transition


def test_linear_path():
    for a, b in [
        (S.new, S.triaged),
        (S.triaged, S.assigned),
        (S.assigned, S.in_progress),
        (S.in_progress, S.waiting_customer),
        (S.in_progress, S.resolved),
        (S.waiting_customer, S.in_progress),
        (S.resolved, S.closed),
        (S.resolved, S.reopened),
    ]:
        assert can_transition(a, b) is True


def test_illegal_transitions_rejected():
    for a, b in [
        (S.new, S.resolved),
        (S.closed, S.reopened),
        (S.resolved, S.new),
        (S.closed, S.in_progress),
    ]:
        assert can_transition(a, b) is False

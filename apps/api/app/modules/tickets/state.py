from app.models.tickets import TicketStatus

ALLOWED_TRANSITIONS: dict[TicketStatus, set[TicketStatus]] = {
    TicketStatus.new: {TicketStatus.triaged},
    TicketStatus.triaged: {TicketStatus.assigned},
    TicketStatus.assigned: {TicketStatus.in_progress},
    TicketStatus.in_progress: {TicketStatus.waiting_customer, TicketStatus.resolved},
    TicketStatus.waiting_customer: {TicketStatus.in_progress, TicketStatus.resolved},
    TicketStatus.resolved: {TicketStatus.closed, TicketStatus.reopened},
    TicketStatus.reopened: {TicketStatus.in_progress, TicketStatus.resolved},
    TicketStatus.closed: set(),
}


def can_transition(current: TicketStatus, target: TicketStatus) -> bool:
    return target in ALLOWED_TRANSITIONS[current]

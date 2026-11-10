package br.uece.eventhub.events;

import java.time.Instant;
import java.util.UUID;

final class EventManagementPolicy {
  private EventManagementPolicy() {}

  static void authorizeCreate(Principal principal, UUID organizationId) {
    requireManager(principal);
    if (principal.role() == Principal.Role.ORGANIZER
        && !principal.organizationId().equals(organizationId)) {
      throw new ApiException(403, "FORBIDDEN", "Organizer cannot manage this organization.");
    }
  }

  static void authorizeOrganization(Principal principal, UUID organizationId) {
    if (principal.role() == Principal.Role.ORGANIZER
        && !organizationId.equals(principal.organizationId())) {
      throw new ApiException(404, "RESOURCE_NOT_FOUND", "Resource was not found.");
    }
  }

  static void requireManager(Principal principal) {
    if (principal.role() != Principal.Role.ORGANIZER
        && principal.role() != Principal.Role.GLOBAL_ADMIN) {
      throw new ApiException(403, "FORBIDDEN", "This identity cannot manage events or activities.");
    }
  }

  static void ensureEventEditable(EventView event) {
    if ("CANCELLED".equals(event.status())) {
      throw new ApiException(
          409, "EVENT_CANCELLED", "Activities cannot be changed for a cancelled event.");
    }
  }

  static void validateActivityPeriod(ActivityInput input, EventView event) {
    if (input.startsAt().isBefore(event.startsAt()) || input.endsAt().isAfter(event.endsAt())) {
      throw new ApiException(
          422, "ACTIVITY_OUTSIDE_EVENT", "Activity dates must be within the event period.");
    }
  }

  static void validateRange(Instant startsAt, Instant endsAt, String resource) {
    if (!startsAt.isBefore(endsAt)) {
      throw new ApiException(
          422, "INVALID_SCHEDULE", resource + " start time must be earlier than its end time.");
    }
  }

  static void validatePage(int limit, int offset) {
    if (limit < 1 || limit > 100 || offset < 0) {
      throw new ApiException(
          400, "INVALID_PAGINATION", "Limit must be 1-100 and offset must be non-negative.");
    }
  }
}

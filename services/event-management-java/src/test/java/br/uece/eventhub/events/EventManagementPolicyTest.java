package br.uece.eventhub.events;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class EventManagementPolicyTest {
  private static final UUID ORGANIZATION_ID =
      UUID.fromString("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
  private static final UUID OTHER_ORGANIZATION_ID =
      UUID.fromString("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb");

  @Test
  void authorizesManagerRolesAndOrganizationScope() {
    Principal organizer =
        new Principal(UUID.randomUUID(), Principal.Role.ORGANIZER, ORGANIZATION_ID);
    Principal admin = new Principal(UUID.randomUUID(), Principal.Role.GLOBAL_ADMIN, null);
    Principal participant = new Principal(UUID.randomUUID(), Principal.Role.PARTICIPANT, null);

    assertThatCode(() -> EventManagementPolicy.authorizeCreate(organizer, ORGANIZATION_ID))
        .doesNotThrowAnyException();
    assertThatCode(() -> EventManagementPolicy.authorizeCreate(admin, OTHER_ORGANIZATION_ID))
        .doesNotThrowAnyException();
    assertThatCode(() -> EventManagementPolicy.authorizeOrganization(organizer, ORGANIZATION_ID))
        .doesNotThrowAnyException();
    assertThatCode(() -> EventManagementPolicy.authorizeOrganization(admin, OTHER_ORGANIZATION_ID))
        .doesNotThrowAnyException();
    assertThatThrownBy(
            () -> EventManagementPolicy.authorizeCreate(organizer, OTHER_ORGANIZATION_ID))
        .satisfies(error -> assertApiError(error, 403, "FORBIDDEN"));
    assertThatThrownBy(() -> EventManagementPolicy.requireManager(participant))
        .satisfies(error -> assertApiError(error, 403, "FORBIDDEN"));
    assertThatThrownBy(
            () -> EventManagementPolicy.authorizeOrganization(organizer, OTHER_ORGANIZATION_ID))
        .satisfies(error -> assertApiError(error, 404, "RESOURCE_NOT_FOUND"));
  }

  @Test
  void rejectsCancelledEventsAndAcceptsOtherStatuses() {
    assertThatCode(() -> EventManagementPolicy.ensureEventEditable(event("DRAFT")))
        .doesNotThrowAnyException();
    assertThatCode(() -> EventManagementPolicy.ensureEventEditable(event("PUBLISHED")))
        .doesNotThrowAnyException();
    assertThatThrownBy(() -> EventManagementPolicy.ensureEventEditable(event("CANCELLED")))
        .satisfies(error -> assertApiError(error, 409, "EVENT_CANCELLED"));
  }

  @Test
  void validatesActivityAndEventTimeBoundaries() {
    Instant start = Instant.parse("2030-01-01T10:00:00Z");
    Instant end = Instant.parse("2030-01-01T12:00:00Z");
    EventView event =
        new EventView(
            UUID.randomUUID(), ORGANIZATION_ID, "Event", "Description", start, end, "DRAFT", 1);
    ActivityInput boundaryActivity =
        new ActivityInput(
            "Activity", start, end, "Location", "Organizer", 10, new MoneyInput(0L, "USD"));

    assertThatCode(() -> EventManagementPolicy.validateRange(start, end, "Event"))
        .doesNotThrowAnyException();
    assertThatCode(() -> EventManagementPolicy.validateActivityPeriod(boundaryActivity, event))
        .doesNotThrowAnyException();
    assertThatThrownBy(() -> EventManagementPolicy.validateRange(end, start, "Event"))
        .satisfies(error -> assertApiError(error, 422, "INVALID_SCHEDULE"));
    assertThatThrownBy(
            () ->
                EventManagementPolicy.validateActivityPeriod(
                    new ActivityInput(
                        "Activity",
                        start.minusSeconds(1),
                        end,
                        "Location",
                        "Organizer",
                        10,
                        new MoneyInput(0L, "USD")),
                    event))
        .satisfies(error -> assertApiError(error, 422, "ACTIVITY_OUTSIDE_EVENT"));
    assertThatThrownBy(
            () ->
                EventManagementPolicy.validateActivityPeriod(
                    new ActivityInput(
                        "Activity",
                        start,
                        end.plusSeconds(1),
                        "Location",
                        "Organizer",
                        10,
                        new MoneyInput(0L, "USD")),
                    event))
        .satisfies(error -> assertApiError(error, 422, "ACTIVITY_OUTSIDE_EVENT"));
  }

  @Test
  void validatesPaginationBoundaries() {
    assertThatCode(() -> EventManagementPolicy.validatePage(1, 0)).doesNotThrowAnyException();
    assertThatCode(() -> EventManagementPolicy.validatePage(100, 0)).doesNotThrowAnyException();
    assertThatCode(() -> EventManagementPolicy.validatePage(20, 1)).doesNotThrowAnyException();
    assertThatThrownBy(() -> EventManagementPolicy.validatePage(0, 0))
        .satisfies(error -> assertApiError(error, 400, "INVALID_PAGINATION"));
    assertThatThrownBy(() -> EventManagementPolicy.validatePage(101, 0))
        .satisfies(error -> assertApiError(error, 400, "INVALID_PAGINATION"));
    assertThatThrownBy(() -> EventManagementPolicy.validatePage(20, -1))
        .satisfies(error -> assertApiError(error, 400, "INVALID_PAGINATION"));
  }

  private static EventView event(String status) {
    return new EventView(
        UUID.randomUUID(),
        ORGANIZATION_ID,
        "Event",
        "Description",
        Instant.parse("2030-01-01T10:00:00Z"),
        Instant.parse("2030-01-01T12:00:00Z"),
        status,
        1);
  }

  private static void assertApiError(Throwable error, int status, String code) {
    assertThat(error).isInstanceOf(ApiException.class);
    ApiException apiError = (ApiException) error;
    assertThat(apiError.status()).isEqualTo(status);
    assertThat(apiError.code()).isEqualTo(code);
  }
}

package br.uece.eventhub.events;

import br.uece.eventhub.events.EventRepository.SnapshotRow;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class EventManagementService {
  private final EventRepository repository;
  private final IdempotencyService idempotency;
  private final IntegrationSettings settings;

  public EventManagementService(
      EventRepository repository, IdempotencyService idempotency, IntegrationSettings settings) {
    this.repository = repository;
    this.idempotency = idempotency;
    this.settings = settings;
  }

  public List<EventView> listPublishedEvents(String query, int limit, int offset) {
    EventManagementPolicy.validatePage(limit, offset);
    if (query != null && query.length() > 200) {
      throw new ApiException(400, "INVALID_QUERY", "Title search cannot exceed 200 characters.");
    }
    return repository.listPublishedEvents(query, limit, offset);
  }

  public EventView getPublishedEvent(UUID eventId) {
    return repository
        .findPublishedEvent(eventId)
        .orElseThrow(() -> notFound("EVENT_NOT_FOUND", "Published event was not found."));
  }

  public List<ActivityView> listPublishedActivities(UUID eventId, int limit, int offset) {
    EventManagementPolicy.validatePage(limit, offset);
    getPublishedEvent(eventId);
    return repository.listActivities(eventId, limit, offset);
  }

  public ActivityView getPublishedActivity(UUID activityId) {
    ActivityView activity =
        repository
            .findActivity(activityId)
            .orElseThrow(() -> notFound("ACTIVITY_NOT_FOUND", "Published activity was not found."));
    getPublishedEvent(activity.eventId());
    return activity;
  }

  public List<EventView> listOrganizationEvents(
      Principal principal, UUID organizationId, int limit, int offset) {
    EventManagementPolicy.authorizeCreate(principal, organizationId);
    EventManagementPolicy.validatePage(limit, offset);
    return repository.listOrganizationEvents(organizationId, limit, offset);
  }

  public List<ActivityView> listOrganizationActivities(
      Principal principal, UUID organizationId, UUID eventId, int limit, int offset) {
    EventManagementPolicy.authorizeCreate(principal, organizationId);
    EventManagementPolicy.validatePage(limit, offset);
    repository
        .findEvent(eventId)
        .filter(event -> event.organizationId().equals(organizationId))
        .orElseThrow(() -> notFound("EVENT_NOT_FOUND", "Event was not found."));
    return repository.listActivities(eventId, limit, offset);
  }

  public CommandResult createEvent(Principal principal, String key, EventInput input) {
    EventManagementPolicy.authorizeCreate(principal, input.organizationId());
    EventManagementPolicy.validateRange(input.startsAt(), input.endsAt(), "Event");
    return idempotency.execute(
        principal.subjectId(),
        "createEvent",
        key,
        input,
        () -> new CommandResult(201, repository.createEvent(input)));
  }

  public CommandResult updateEvent(
      Principal principal, UUID eventId, String key, EventInput input, UUID correlationId) {
    EventManagementPolicy.validateRange(input.startsAt(), input.endsAt(), "Event");
    return idempotency.execute(
        principal.subjectId(),
        "updateEvent:" + eventId,
        key,
        input,
        () -> {
          EventView event = ownedEvent(principal, eventId);
          repository.lockEvent(eventId);
          event = repository.findEvent(eventId).orElseThrow();
          if (!event.organizationId().equals(input.organizationId())) {
            throw conflict(
                "ORGANIZATION_REASSIGNMENT_FORBIDDEN",
                "An event cannot be reassigned to another organization.");
          }
          if ("CANCELLED".equals(event.status())) {
            throw conflict("EVENT_CANCELLED", "A cancelled event cannot be updated.");
          }
          if (!repository.activitiesFitEvent(eventId, input.startsAt(), input.endsAt())) {
            throw ineligible(
                "ACTIVITY_OUTSIDE_EVENT", "The updated event period excludes an activity.");
          }
          if (repository.updateEvent(eventId, input) != 1) {
            throw conflict("EVENT_STATE_CONFLICT", "The event could not be updated.");
          }
          if ("PUBLISHED".equals(event.status())) {
            publishActivitySnapshots(eventId, "PUBLISHED", correlationId);
          }
          return new CommandResult(200, repository.findEvent(eventId).orElseThrow());
        });
  }

  public CommandResult publishEvent(
      Principal principal, UUID eventId, String key, UUID correlationId) {
    return idempotency.execute(
        principal.subjectId(),
        "publishEvent:" + eventId,
        key,
        eventId,
        () -> {
          EventView event = ownedEvent(principal, eventId);
          repository.lockEvent(eventId);
          event = repository.findEvent(eventId).orElseThrow();
          if ("CANCELLED".equals(event.status())) {
            throw conflict("EVENT_CANCELLED", "A cancelled event cannot be published.");
          }
          if ("PUBLISHED".equals(event.status())) {
            return new CommandResult(200, event);
          }
          if (repository.countActivities(eventId) == 0
              || !repository.activitiesFitEvent(eventId, event.startsAt(), event.endsAt())) {
            throw ineligible(
                "EVENT_SCHEDULE_INELIGIBLE",
                "An event must contain activities within its scheduled period before publication.");
          }
          repository.setEventStatus(eventId, "PUBLISHED");
          publishActivitySnapshots(eventId, "PUBLISHED", correlationId);
          return new CommandResult(200, repository.findEvent(eventId).orElseThrow());
        });
  }

  public CommandResult cancelEvent(
      Principal principal, UUID eventId, String key, UUID correlationId) {
    return idempotency.execute(
        principal.subjectId(),
        "cancelEvent:" + eventId,
        key,
        eventId,
        () -> {
          EventView event = ownedEvent(principal, eventId);
          repository.lockEvent(eventId);
          event = repository.findEvent(eventId).orElseThrow();
          if ("CANCELLED".equals(event.status())) {
            return new CommandResult(200, event);
          }
          repository.setEventStatus(eventId, "CANCELLED");
          if ("PUBLISHED".equals(event.status())) {
            publishActivitySnapshots(eventId, "CANCELLED", correlationId);
          }
          return new CommandResult(200, repository.findEvent(eventId).orElseThrow());
        });
  }

  public CommandResult createActivity(
      Principal principal, UUID eventId, String key, ActivityInput input, UUID correlationId) {
    EventManagementPolicy.validateRange(input.startsAt(), input.endsAt(), "Activity");
    return idempotency.execute(
        principal.subjectId(),
        "createActivity:" + eventId,
        key,
        input,
        () -> {
          EventView event = ownedEvent(principal, eventId);
          repository.lockEvent(eventId);
          event = repository.findEvent(eventId).orElseThrow();
          EventManagementPolicy.ensureEventEditable(event);
          EventManagementPolicy.validateActivityPeriod(input, event);
          ActivityView activity = repository.createActivity(eventId, input);
          if ("PUBLISHED".equals(event.status())) {
            repository.enqueueActivityChanged(activity, event.status(), correlationId);
          }
          return new CommandResult(201, activity);
        });
  }

  public CommandResult updateActivity(
      Principal principal, UUID activityId, String key, ActivityInput input, UUID correlationId) {
    EventManagementPolicy.validateRange(input.startsAt(), input.endsAt(), "Activity");
    return idempotency.execute(
        principal.subjectId(),
        "updateActivity:" + activityId,
        key,
        input,
        () -> {
          ActivityView activity = ownedActivity(principal, activityId);
          repository.lockEvent(activity.eventId());
          activity = repository.findActivityForUpdate(activityId).orElseThrow();
          EventView event =
              repository
                  .findEvent(activity.eventId())
                  .orElseThrow(() -> notFound("EVENT_NOT_FOUND", "Event was not found."));
          EventManagementPolicy.ensureEventEditable(event);
          EventManagementPolicy.validateActivityPeriod(input, event);
          if (activity.capacityFrozen() && activity.capacity() != input.capacity()) {
            throw conflict(
                "CAPACITY_FROZEN", "Activity capacity cannot change after admission is activated.");
          }
          if (repository.updateActivity(activityId, input) != 1) {
            throw conflict(
                "ACTIVITY_STATE_CONFLICT", "The activity could not be updated with this capacity.");
          }
          ActivityView updated = repository.findActivity(activityId).orElseThrow();
          if ("PUBLISHED".equals(event.status())) {
            repository.enqueueActivityChanged(updated, event.status(), correlationId);
          }
          return new CommandResult(200, updated);
        });
  }

  @Transactional
  public AdmissionSnapshotView activateOrValidateAdmission(UUID activityId, String serviceKey) {
    if (!constantTimeEquals(settings.registrationServiceKey(), serviceKey)) {
      throw new ApiException(
          401, "UNAUTHORIZED_SERVICE", "A valid Registration service credential is required.");
    }
    ActivityView activity =
        repository
            .findActivity(activityId)
            .orElseThrow(() -> notFound("ACTIVITY_NOT_FOUND", "Activity was not found."));
    repository.lockEvent(activity.eventId());
    activity =
        repository
            .findActivityForUpdate(activityId)
            .orElseThrow(() -> notFound("ACTIVITY_NOT_FOUND", "Activity was not found."));
    EventView event =
        repository
            .findEvent(activity.eventId())
            .orElseThrow(() -> notFound("EVENT_NOT_FOUND", "Event was not found."));
    if (!"PUBLISHED".equals(event.status()) || !activity.startsAt().isAfter(Instant.now())) {
      throw ineligible(
          "ADMISSION_INELIGIBLE",
          "Admission requires a published event and an activity that has not started.");
    }

    SnapshotRow snapshot = repository.findSnapshot(activityId).orElse(null);
    if (snapshot == null) {
      repository.freezeCapacity(activityId);
      activity = repository.findActivity(activityId).orElseThrow();
      snapshot = repository.createSnapshot(activityId, activity.version(), activity.capacity());
    }
    return new AdmissionSnapshotView(
        repository.findActivity(activityId).orElseThrow(),
        snapshot.snapshotId(),
        snapshot.snapshotVersion(),
        snapshot.frozenCapacity());
  }

  private EventView ownedEvent(Principal principal, UUID eventId) {
    EventManagementPolicy.requireManager(principal);
    EventView event =
        repository
            .findEvent(eventId)
            .orElseThrow(() -> notFound("EVENT_NOT_FOUND", "Event was not found."));
    EventManagementPolicy.authorizeOrganization(principal, event.organizationId());
    return event;
  }

  private ActivityView ownedActivity(Principal principal, UUID activityId) {
    EventManagementPolicy.requireManager(principal);
    ActivityView activity =
        repository
            .findActivity(activityId)
            .orElseThrow(() -> notFound("ACTIVITY_NOT_FOUND", "Activity was not found."));
    EventManagementPolicy.authorizeOrganization(principal, activity.organizationId());
    return activity;
  }

  private void publishActivitySnapshots(UUID eventId, String status, UUID correlationId) {
    repository.incrementActivityVersions(eventId);
    for (ActivityView activity : repository.listActivitiesForEvent(eventId)) {
      repository.enqueueActivityChanged(activity, status, correlationId);
    }
  }

  private static boolean constantTimeEquals(String expected, String supplied) {
    byte[] expectedBytes = expected.getBytes(java.nio.charset.StandardCharsets.UTF_8);
    byte[] suppliedBytes =
        (supplied == null ? "" : supplied).getBytes(java.nio.charset.StandardCharsets.UTF_8);
    return java.security.MessageDigest.isEqual(expectedBytes, suppliedBytes);
  }

  private static ApiException notFound(String code, String message) {
    return new ApiException(404, code, message);
  }

  private static ApiException conflict(String code, String message) {
    return new ApiException(409, code, message);
  }

  private static ApiException ineligible(String code, String message) {
    return new ApiException(422, code, message);
  }
}

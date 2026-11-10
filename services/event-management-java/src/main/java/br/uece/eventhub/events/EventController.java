package br.uece.eventhub.events;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import java.util.List;
import java.util.UUID;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class EventController {
  private final EventManagementService service;
  private final IdentityClient identityClient;

  public EventController(EventManagementService service, IdentityClient identityClient) {
    this.service = service;
    this.identityClient = identityClient;
  }

  @GetMapping("/events")
  public List<EventView> listEvents(
      @RequestParam(required = false) String q,
      @RequestParam(defaultValue = "20") int limit,
      @RequestParam(defaultValue = "0") int offset) {
    return service.listPublishedEvents(q, limit, offset);
  }

  @PostMapping("/events")
  public ResponseEntity<?> createEvent(
      @Valid @RequestBody EventInput input,
      @RequestHeader(value = "Idempotency-Key", required = false) String idempotencyKey,
      HttpServletRequest request) {
    return command(service.createEvent(principal(request), idempotencyKey, input));
  }

  @GetMapping("/events/{eventId}")
  public EventView getEvent(@PathVariable UUID eventId) {
    return service.getPublishedEvent(eventId);
  }

  @PutMapping("/events/{eventId}")
  public ResponseEntity<?> updateEvent(
      @PathVariable UUID eventId,
      @Valid @RequestBody EventInput input,
      @RequestHeader(value = "Idempotency-Key", required = false) String idempotencyKey,
      HttpServletRequest request) {
    return command(service.updateEvent(principal(request), eventId, idempotencyKey, input));
  }

  @PostMapping("/events/{eventId}/publish")
  public ResponseEntity<?> publishEvent(
      @PathVariable UUID eventId,
      @RequestHeader(value = "Idempotency-Key", required = false) String idempotencyKey,
      HttpServletRequest request) {
    return command(service.publishEvent(principal(request), eventId, idempotencyKey));
  }

  @PostMapping("/events/{eventId}/cancel")
  public ResponseEntity<?> cancelEvent(
      @PathVariable UUID eventId,
      @RequestHeader(value = "Idempotency-Key", required = false) String idempotencyKey,
      HttpServletRequest request) {
    return command(service.cancelEvent(principal(request), eventId, idempotencyKey));
  }

  @GetMapping("/events/{eventId}/activities")
  public List<ActivityView> listActivities(
      @PathVariable UUID eventId,
      @RequestParam(defaultValue = "20") int limit,
      @RequestParam(defaultValue = "0") int offset) {
    return service.listPublishedActivities(eventId, limit, offset);
  }

  @PostMapping("/events/{eventId}/activities")
  public ResponseEntity<?> createActivity(
      @PathVariable UUID eventId,
      @Valid @RequestBody ActivityInput input,
      @RequestHeader(value = "Idempotency-Key", required = false) String idempotencyKey,
      HttpServletRequest request) {
    return command(service.createActivity(principal(request), eventId, idempotencyKey, input));
  }

  @GetMapping("/activities/{activityId}")
  public ActivityView getActivity(@PathVariable UUID activityId) {
    return service.getPublishedActivity(activityId);
  }

  @PutMapping("/activities/{activityId}")
  public ResponseEntity<?> updateActivity(
      @PathVariable UUID activityId,
      @Valid @RequestBody ActivityInput input,
      @RequestHeader(value = "Idempotency-Key", required = false) String idempotencyKey,
      HttpServletRequest request) {
    return command(service.updateActivity(principal(request), activityId, idempotencyKey, input));
  }

  @PostMapping("/internal/activities/{activityId}/admission")
  public AdmissionSnapshotView admission(
      @PathVariable UUID activityId,
      @RequestHeader(value = "X-Service-Key", required = false) String serviceKey) {
    return service.activateOrValidateAdmission(activityId, serviceKey);
  }

  private Principal principal(HttpServletRequest request) {
    String authorization = request.getHeader("Authorization");
    if (authorization == null
        || !authorization.startsWith("Bearer ")
        || authorization.substring("Bearer ".length()).isBlank()) {
      throw new ApiException(401, "UNAUTHENTICATED", "A valid bearer credential is required.");
    }
    String correlationId = request.getAttribute(CorrelationIdFilter.ATTRIBUTE).toString();
    return identityClient.validate(authorization.substring("Bearer ".length()), correlationId);
  }

  private static ResponseEntity<?> command(CommandResult result) {
    return ResponseEntity.status(result.status()).body(result.body());
  }
}

package br.uece.eventhub.events;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;

import com.fasterxml.jackson.databind.JsonNode;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@ActiveProfiles("test")
class EventManagementContractTest {
  private static final UUID ORGANIZATION_ID =
      UUID.fromString("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
  private static final UUID OTHER_ORGANIZATION_ID =
      UUID.fromString("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb");
  private static final UUID ORGANIZER_ID = UUID.fromString("cccccccc-cccc-4ccc-8ccc-cccccccccccc");
  private static final UUID ADMIN_ID = UUID.fromString("dddddddd-dddd-4ddd-8ddd-dddddddddddd");
  private static final UUID PARTICIPANT_ID =
      UUID.fromString("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee");

  @Autowired private TestRestTemplate client;
  @Autowired private JdbcTemplate jdbcTemplate;
  @MockBean private IdentityClient identityClient;

  @BeforeEach
  void resetDatabaseAndIdentity() {
    jdbcTemplate.update("DELETE FROM event_outbox");
    jdbcTemplate.update("DELETE FROM command_receipt");
    jdbcTemplate.update("DELETE FROM admission_snapshot");
    jdbcTemplate.update("DELETE FROM activity");
    jdbcTemplate.update("DELETE FROM event");
    when(identityClient.validate(eq("organizer-token"), anyString()))
        .thenReturn(new Principal(ORGANIZER_ID, Principal.Role.ORGANIZER, ORGANIZATION_ID));
    when(identityClient.validate(eq("admin-token"), anyString()))
        .thenReturn(new Principal(ADMIN_ID, Principal.Role.GLOBAL_ADMIN, null));
    when(identityClient.validate(eq("other-organizer-token"), anyString()))
        .thenReturn(new Principal(ORGANIZER_ID, Principal.Role.ORGANIZER, OTHER_ORGANIZATION_ID));
    when(identityClient.validate(eq("participant-token"), anyString()))
        .thenReturn(new Principal(PARTICIPANT_ID, Principal.Role.PARTICIPANT, null));
  }

  @Test
  void publicCatalogExposesOnlyPublishedEventsAndActivities() throws Exception {
    ResponseEntity<JsonNode> draft = createEvent("Catalog Event", "create-event-1");
    assertThat(draft.getStatusCode().value()).isEqualTo(201);
    UUID eventId = UUID.fromString(draft.getBody().path("id").asText());
    ResponseEntity<JsonNode> laterActivity =
        createActivity(eventId, "Later activity", "2030-01-01T12:00:00Z", "create-activity-1");
    assertThat(laterActivity.getStatusCode().value()).isEqualTo(201);
    ResponseEntity<JsonNode> earlierActivity =
        createActivity(eventId, "Earlier activity", "2030-01-01T10:00:00Z", "create-activity-2");
    assertThat(earlierActivity.getStatusCode().value()).isEqualTo(201);

    ResponseEntity<JsonNode> beforePublication = client.getForEntity("/events", JsonNode.class);
    assertThat(beforePublication.getStatusCode().value()).isEqualTo(200);
    assertThat(beforePublication.getBody()).isEmpty();
    assertThat(client.getForEntity("/events/" + eventId, JsonNode.class).getStatusCode().value())
        .isEqualTo(404);
    assertThat(
            client
                .getForEntity("/events/" + eventId + "/activities", JsonNode.class)
                .getStatusCode()
                .value())
        .isEqualTo(404);
    assertThat(
            client
                .getForEntity(
                    "/activities/" + laterActivity.getBody().path("id").asText(), JsonNode.class)
                .getStatusCode()
                .value())
        .isEqualTo(404);

    ResponseEntity<JsonNode> published = post("/events/" + eventId + "/publish", null, "publish-1");
    assertThat(published.getStatusCode().value()).isEqualTo(200);
    assertThat(outboxCount()).isEqualTo(2);
    assertThat(eventSnapshotStatus(UUID.fromString(laterActivity.getBody().path("id").asText()), 2))
        .isEqualTo("PUBLISHED");
    assertThat(
            eventSnapshotStatus(UUID.fromString(earlierActivity.getBody().path("id").asText()), 2))
        .isEqualTo("PUBLISHED");
    post("/events/" + eventId + "/publish", null, "publish-replay-new-key");
    assertThat(outboxCount()).isEqualTo(2);
    ResponseEntity<JsonNode> catalog = client.getForEntity("/events?q=cAtAlOg", JsonNode.class);
    assertThat(catalog.getStatusCode().value()).isEqualTo(200);
    assertThat(catalog.getBody()).hasSize(1);
    assertThat(catalog.getBody().get(0).path("status").asText()).isEqualTo("PUBLISHED");

    ResponseEntity<JsonNode> activities =
        client.getForEntity("/events/" + eventId + "/activities", JsonNode.class);
    assertThat(activities.getStatusCode().value()).isEqualTo(200);
    assertThat(activities.getBody()).hasSize(2);
    assertThat(activities.getBody().get(0).path("title").asText()).isEqualTo("Earlier activity");
    assertThat(
            client
                .getForEntity(
                    "/activities/" + activities.getBody().get(0).path("id").asText(),
                    JsonNode.class)
                .getStatusCode()
                .value())
        .isEqualTo(200);

    ResponseEntity<JsonNode> cancelled =
        post("/events/" + eventId + "/cancel", null, "cancel-event");
    assertThat(cancelled.getStatusCode().value()).isEqualTo(200);
    assertThat(cancelled.getBody().path("status").asText()).isEqualTo("CANCELLED");
    assertThat(outboxCount()).isEqualTo(4);
    assertThat(eventSnapshotStatus(UUID.fromString(laterActivity.getBody().path("id").asText()), 3))
        .isEqualTo("CANCELLED");
    assertThat(client.getForEntity("/events", JsonNode.class).getBody()).isEmpty();
    assertThat(client.getForEntity("/events/" + eventId, JsonNode.class).getStatusCode().value())
        .isEqualTo(404);
    assertThat(
            client
                .getForEntity("/events/" + eventId + "/activities", JsonNode.class)
                .getStatusCode()
                .value())
        .isEqualTo(404);
    assertThat(post("/events/" + eventId + "/publish", null, "republish").getStatusCode().value())
        .isEqualTo(409);
  }

  @Test
  void creationIsIdempotentAndOrganizationScopeIsEnforced() {
    ResponseEntity<JsonNode> first = createEvent("Scoped Event", "same-key");
    ResponseEntity<JsonNode> replay = createEvent("Scoped Event", "same-key");
    assertThat(first.getStatusCode().value()).isEqualTo(201);
    assertThat(replay.getStatusCode().value()).isEqualTo(201);
    assertThat(replay.getBody().path("id").asText()).isEqualTo(first.getBody().path("id").asText());

    ResponseEntity<JsonNode> changedPayload =
        post("/events", eventInput("Different Event", ORGANIZATION_ID), "same-key");
    assertThat(changedPayload.getStatusCode().value()).isEqualTo(409);
    assertThat(changedPayload.getBody().path("code").asText()).isEqualTo("IDEMPOTENCY_CONFLICT");

    UUID eventId = UUID.fromString(first.getBody().path("id").asText());
    HttpHeaders headers = headers("other-organizer-token", "foreign-update");
    ResponseEntity<JsonNode> foreignUpdate =
        client.exchange(
            "/events/" + eventId,
            HttpMethod.PUT,
            new HttpEntity<>(eventInput("Foreign Update", ORGANIZATION_ID), headers),
            JsonNode.class);
    assertThat(foreignUpdate.getStatusCode().value()).isEqualTo(404);

    ResponseEntity<JsonNode> anonymousCreate =
        client.postForEntity(
            "/events",
            new HttpEntity<>(eventInput("Anonymous", ORGANIZATION_ID), jsonHeaders()),
            JsonNode.class);
    assertThat(anonymousCreate.getStatusCode().value()).isEqualTo(401);

    ResponseEntity<JsonNode> wrongOrganization =
        post("/events", eventInput("Wrong Organization", OTHER_ORGANIZATION_ID), "wrong-org");
    assertThat(wrongOrganization.getStatusCode().value()).isEqualTo(403);

    HttpHeaders participantHeaders = headers("participant-token", "participant-create");
    participantHeaders.set("X-Role", "GLOBAL_ADMIN");
    ResponseEntity<JsonNode> participantCreate =
        client.exchange(
            "/events",
            HttpMethod.POST,
            new HttpEntity<>(eventInput("Not Authorized", ORGANIZATION_ID), participantHeaders),
            JsonNode.class);
    assertThat(participantCreate.getStatusCode().value()).isEqualTo(403);

    ResponseEntity<JsonNode> adminCreate =
        client.postForEntity(
            "/events",
            new HttpEntity<>(
                eventInput("Admin Event", OTHER_ORGANIZATION_ID),
                headers("admin-token", "admin-create")),
            JsonNode.class);
    assertThat(adminCreate.getStatusCode().value()).isEqualTo(201);
    assertThat(adminCreate.getBody().path("organizationId").asText())
        .isEqualTo(OTHER_ORGANIZATION_ID.toString());
  }

  @Test
  void concurrentRetriesCreateOnlyOneEvent() throws Exception {
    String key = "concurrent-create";
    ExecutorService executor = Executors.newFixedThreadPool(2);
    try {
      Future<ResponseEntity<JsonNode>> first =
          executor.submit(() -> createEvent("Concurrent Event", key));
      Future<ResponseEntity<JsonNode>> retry =
          executor.submit(() -> createEvent("Concurrent Event", key));
      ResponseEntity<JsonNode> firstResponse = first.get(10, TimeUnit.SECONDS);
      ResponseEntity<JsonNode> retryResponse = retry.get(10, TimeUnit.SECONDS);

      assertThat(firstResponse.getStatusCode().value()).isEqualTo(201);
      assertThat(retryResponse.getStatusCode().value()).isEqualTo(201);
      assertThat(retryResponse.getBody().path("id").asText())
          .isEqualTo(firstResponse.getBody().path("id").asText());
      assertThat(jdbcTemplate.queryForObject("SELECT COUNT(*) FROM event", Integer.class))
          .isEqualTo(1);
    } finally {
      executor.shutdownNow();
    }
  }

  @Test
  void admissionFreezesCapacityAndRetriesReturnTheSameSnapshot() {
    UUID eventId =
        UUID.fromString(
            createEvent("Capacity Event", "event-capacity").getBody().path("id").asText());
    ResponseEntity<JsonNode> activity =
        createActivity(eventId, "Limited activity", "2030-01-01T10:00:00Z", "activity-capacity");
    UUID activityId = UUID.fromString(activity.getBody().path("id").asText());
    post("/events/" + eventId + "/publish", null, "publish-capacity");

    HttpHeaders serviceHeaders = new HttpHeaders();
    serviceHeaders.set("X-Service-Key", "synthetic-registration-only");
    ResponseEntity<JsonNode> first =
        client.postForEntity(
            "/internal/activities/" + activityId + "/admission",
            new HttpEntity<>(serviceHeaders),
            JsonNode.class);
    ResponseEntity<JsonNode> retry =
        client.postForEntity(
            "/internal/activities/" + activityId + "/admission",
            new HttpEntity<>(serviceHeaders),
            JsonNode.class);
    assertThat(first.getStatusCode().value()).isEqualTo(200);
    assertThat(retry.getBody().path("snapshotId").asText())
        .isEqualTo(first.getBody().path("snapshotId").asText());
    assertThat(first.getBody().path("frozenCapacity").asInt()).isEqualTo(10);

    ResponseEntity<JsonNode> sameCapacity =
        client.exchange(
            "/activities/" + activityId,
            HttpMethod.PUT,
            new HttpEntity<>(
                activityInput("Renamed activity", "2030-01-01T10:00:00Z", 10),
                headers("organizer-token", "keep-capacity")),
            JsonNode.class);
    assertThat(sameCapacity.getStatusCode().value()).isEqualTo(200);
    assertThat(sameCapacity.getBody().path("capacityFrozen").asBoolean()).isTrue();

    Object changed = activityInput("Limited activity", "2030-01-01T10:00:00Z", 11);
    ResponseEntity<JsonNode> capacityChange =
        client.exchange(
            "/activities/" + activityId,
            HttpMethod.PUT,
            new HttpEntity<>(changed, headers("organizer-token", "change-capacity")),
            JsonNode.class);
    assertThat(capacityChange.getStatusCode().value()).isEqualTo(409);

    serviceHeaders.set("X-Service-Key", "synthetic-unit-only");
    ResponseEntity<JsonNode> sharedKey =
        client.postForEntity(
            "/internal/activities/" + activityId + "/admission",
            new HttpEntity<>(serviceHeaders),
            JsonNode.class);
    assertThat(sharedKey.getStatusCode().value()).isEqualTo(401);
  }

  @Test
  void rejectsInvalidSchedulesAndAdmissionForDraftEvents() {
    UUID eventId =
        UUID.fromString(
            createEvent("Schedule Event", "event-schedule").getBody().path("id").asText());
    ResponseEntity<JsonNode> outsideActivity =
        createActivity(eventId, "Outside activity", "2030-01-03T10:00:00Z", "outside-activity");
    assertThat(outsideActivity.getStatusCode().value()).isEqualTo(422);
    assertThat(
            post("/events/" + eventId + "/publish", null, "publish-empty").getStatusCode().value())
        .isEqualTo(422);
    ResponseEntity<JsonNode> validActivity =
        createActivity(eventId, "Draft activity", "2030-01-01T10:00:00Z", "draft-activity");
    UUID activityId = UUID.fromString(validActivity.getBody().path("id").asText());

    HttpHeaders serviceHeaders = new HttpHeaders();
    serviceHeaders.set("X-Service-Key", "synthetic-registration-only");
    ResponseEntity<JsonNode> draftAdmission =
        client.postForEntity(
            "/internal/activities/" + activityId + "/admission",
            new HttpEntity<>(serviceHeaders),
            JsonNode.class);
    assertThat(draftAdmission.getStatusCode().value()).isEqualTo(422);
  }

  @Test
  void invalidCorrelationIdIsRejectedAndReplacedForTheError() {
    HttpHeaders headers = new HttpHeaders();
    headers.set("X-Correlation-ID", "not-a-uuid");
    ResponseEntity<JsonNode> response =
        client.exchange("/events", HttpMethod.GET, new HttpEntity<>(headers), JsonNode.class);
    assertThat(response.getStatusCode().value()).isEqualTo(400);
    assertThat(response.getHeaders().getFirst("X-Correlation-ID")).isNotBlank();
    assertThat(response.getBody().path("correlationId").asText())
        .isEqualTo(response.getHeaders().getFirst("X-Correlation-ID"));
  }

  private ResponseEntity<JsonNode> createEvent(String title, String key) {
    return post("/events", eventInput(title, ORGANIZATION_ID), key);
  }

  private ResponseEntity<JsonNode> createActivity(
      UUID eventId, String title, String startsAt, String key) {
    return client.postForEntity(
        "/events/" + eventId + "/activities",
        new HttpEntity<>(activityInput(title, startsAt, 10), headers("organizer-token", key)),
        JsonNode.class);
  }

  private ResponseEntity<JsonNode> post(String path, Object body, String key) {
    return client.postForEntity(
        path, new HttpEntity<>(body, headers("organizer-token", key)), JsonNode.class);
  }

  private static HttpHeaders headers(String token, String key) {
    HttpHeaders headers = jsonHeaders();
    headers.setBearerAuth(token);
    headers.set("Idempotency-Key", key);
    headers.set("X-Correlation-ID", UUID.randomUUID().toString());
    return headers;
  }

  private static HttpHeaders jsonHeaders() {
    HttpHeaders headers = new HttpHeaders();
    headers.setContentType(MediaType.APPLICATION_JSON);
    return headers;
  }

  private static Object eventInput(String title, UUID organizationId) {
    return java.util.Map.of(
        "organizationId",
        organizationId,
        "title",
        title,
        "description",
        "Synthetic event description",
        "startsAt",
        "2030-01-01T09:00:00Z",
        "endsAt",
        "2030-01-02T18:00:00Z");
  }

  private static Object activityInput(String title, String startsAt, int capacity) {
    return java.util.Map.of(
        "title",
        title,
        "startsAt",
        startsAt,
        "endsAt",
        java.time.Instant.parse(startsAt).plusSeconds(3600).toString(),
        "location",
        "Campus",
        "responsible",
        "Synthetic Organizer",
        "capacity",
        capacity,
        "price",
        java.util.Map.of("amountMinor", 0, "currency", "BRL"));
  }

  private int outboxCount() {
    return jdbcTemplate.queryForObject(
        "SELECT COUNT(*) FROM event_outbox WHERE routing_key = 'activity.changed'", Integer.class);
  }

  private String eventSnapshotStatus(UUID activityId, long aggregateVersion) throws Exception {
    String body =
        jdbcTemplate.queryForObject(
            "SELECT body FROM event_outbox WHERE aggregate_id = ? AND aggregate_version = ?",
            String.class,
            activityId,
            aggregateVersion);
    return new com.fasterxml.jackson.databind.ObjectMapper()
        .readTree(body)
        .path("data")
        .path("status")
        .asText();
  }
}

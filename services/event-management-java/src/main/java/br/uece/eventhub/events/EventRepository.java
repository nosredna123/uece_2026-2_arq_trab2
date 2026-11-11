package br.uece.eventhub.events;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

@Repository
public class EventRepository {
  private final JdbcTemplate jdbcTemplate;
  private final ObjectMapper objectMapper;

  public EventRepository(JdbcTemplate jdbcTemplate, ObjectMapper objectMapper) {
    this.jdbcTemplate = jdbcTemplate;
    this.objectMapper = objectMapper;
  }

  public EventView createEvent(EventInput input) {
    UUID id = UUID.randomUUID();
    jdbcTemplate.update(
        """
        INSERT INTO event (
          id, organization_id, title, description, starts_at, ends_at, status, version
        ) VALUES (?, ?, ?, ?, ?, ?, 'DRAFT', 1)
        """,
        id,
        input.organizationId(),
        input.title(),
        input.description(),
        timestamp(input.startsAt()),
        timestamp(input.endsAt()));
    return findEvent(id).orElseThrow();
  }

  public Optional<EventView> findEvent(UUID id) {
    return queryOne(
        "SELECT id, organization_id, title, description, starts_at, ends_at, status, version "
            + "FROM event WHERE id = ?",
        EVENT_MAPPER,
        id);
  }

  public Optional<EventView> findPublishedEvent(UUID id) {
    return queryOne(
        "SELECT id, organization_id, title, description, starts_at, ends_at, status, version "
            + "FROM event WHERE id = ? AND status = 'PUBLISHED'",
        EVENT_MAPPER,
        id);
  }

  public void lockEvent(UUID id) {
    jdbcTemplate.query(
        "SELECT id FROM event WHERE id = ? FOR UPDATE",
        (rs, rowNum) -> rs.getObject("id", UUID.class),
        id);
  }

  public List<EventView> listPublishedEvents(String query, int limit, int offset) {
    String titleQuery = query == null ? "" : query.toLowerCase(java.util.Locale.ROOT);
    return jdbcTemplate.query(
        """
        SELECT id, organization_id, title, description, starts_at, ends_at, status, version
        FROM event
        WHERE status = 'PUBLISHED' AND LOWER(title) LIKE ?
        ORDER BY id
        LIMIT ? OFFSET ?
        """,
        EVENT_MAPPER,
        "%" + titleQuery + "%",
        limit,
        offset);
  }

  public List<EventView> listOrganizationEvents(UUID organizationId, int limit, int offset) {
    return jdbcTemplate.query(
        """
        SELECT id, organization_id, title, description, starts_at, ends_at, status, version
        FROM event
        WHERE organization_id = ?
        ORDER BY starts_at, id
        LIMIT ? OFFSET ?
        """,
        EVENT_MAPPER,
        organizationId,
        limit,
        offset);
  }

  public int updateEvent(UUID id, EventInput input) {
    return jdbcTemplate.update(
        """
        UPDATE event SET title = ?, description = ?, starts_at = ?, ends_at = ?, version = version + 1
        WHERE id = ? AND status <> 'CANCELLED'
        """,
        input.title(),
        input.description(),
        timestamp(input.startsAt()),
        timestamp(input.endsAt()),
        id);
  }

  public int setEventStatus(UUID id, String status) {
    return jdbcTemplate.update(
        "UPDATE event SET status = ?, version = version + 1 WHERE id = ?", status, id);
  }

  public ActivityView createActivity(UUID eventId, ActivityInput input) {
    UUID id = UUID.randomUUID();
    jdbcTemplate.update(
        """
        INSERT INTO activity (
          id, event_id, title, starts_at, ends_at, location, responsible,
          capacity, amount_minor, currency, version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        id,
        eventId,
        input.title(),
        timestamp(input.startsAt()),
        timestamp(input.endsAt()),
        input.location(),
        input.responsible(),
        input.capacity(),
        input.price().amountMinor(),
        input.price().currency());
    return findActivity(id).orElseThrow();
  }

  public Optional<ActivityView> findActivity(UUID id) {
    return queryOne(
        """
        SELECT a.id, a.event_id, e.organization_id, a.title, a.starts_at, a.ends_at,
               a.location, a.responsible, a.capacity, a.amount_minor, a.currency,
               a.version, a.capacity_frozen
        FROM activity a JOIN event e ON e.id = a.event_id
        WHERE a.id = ?
        """,
        ACTIVITY_MAPPER,
        id);
  }

  public Optional<ActivityView> findActivityForUpdate(UUID id) {
    jdbcTemplate.query(
        "SELECT id FROM activity WHERE id = ? FOR UPDATE",
        (rs, rowNum) -> rs.getObject("id", UUID.class),
        id);
    return findActivity(id);
  }

  public List<ActivityView> listActivities(UUID eventId, int limit, int offset) {
    return jdbcTemplate.query(
        """
        SELECT a.id, a.event_id, e.organization_id, a.title, a.starts_at, a.ends_at,
               a.location, a.responsible, a.capacity, a.amount_minor, a.currency,
               a.version, a.capacity_frozen
        FROM activity a JOIN event e ON e.id = a.event_id
        WHERE a.event_id = ?
        ORDER BY a.starts_at, a.id
        LIMIT ? OFFSET ?
        """,
        ACTIVITY_MAPPER,
        eventId,
        limit,
        offset);
  }

  public List<ActivityView> listActivitiesForEvent(UUID eventId) {
    return jdbcTemplate.query(
        """
        SELECT a.id, a.event_id, e.organization_id, a.title, a.starts_at, a.ends_at,
               a.location, a.responsible, a.capacity, a.amount_minor, a.currency,
               a.version, a.capacity_frozen
        FROM activity a JOIN event e ON e.id = a.event_id
        WHERE a.event_id = ?
        ORDER BY a.starts_at, a.id
        """,
        ACTIVITY_MAPPER,
        eventId);
  }

  public void incrementActivityVersions(UUID eventId) {
    jdbcTemplate.update("UPDATE activity SET version = version + 1 WHERE event_id = ?", eventId);
  }

  public void enqueueActivityChanged(ActivityView activity, String status, UUID correlationId) {
    OffsetDateTime occurredAt = OffsetDateTime.now(ZoneOffset.UTC);
    UUID eventId = UUID.randomUUID();
    ActivityChangedEvent event =
        new ActivityChangedEvent(
            eventId,
            "activity.changed",
            "1.0.0",
            activity.id(),
            activity.version(),
            correlationId,
            occurredAt,
            "event-management",
            new ActivityChangedData(
                activity.id(),
                activity.eventId(),
                activity.organizationId(),
                status,
                activity.startsAt(),
                activity.endsAt()));
    try {
      jdbcTemplate.update(
          "INSERT INTO event_outbox (event_id, aggregate_id, aggregate_version, routing_key, "
              + "correlation_id, body, created_at, available_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
          eventId,
          activity.id(),
          activity.version(),
          "activity.changed",
          correlationId,
          objectMapper.writeValueAsString(event),
          occurredAt,
          occurredAt);
    } catch (JsonProcessingException exception) {
      throw new ApiException(
          500, "EVENT_SERIALIZATION_FAILED", "Activity snapshot could not be persisted.");
    }
  }

  public int countActivities(UUID eventId) {
    Integer count =
        jdbcTemplate.queryForObject(
            "SELECT COUNT(*) FROM activity WHERE event_id = ?", Integer.class, eventId);
    return count == null ? 0 : count;
  }

  public boolean activitiesFitEvent(UUID eventId, Instant startsAt, Instant endsAt) {
    Integer count =
        jdbcTemplate.queryForObject(
            """
            SELECT COUNT(*) FROM activity
            WHERE event_id = ? AND (starts_at < ? OR ends_at > ?)
            """,
            Integer.class,
            eventId,
            timestamp(startsAt),
            timestamp(endsAt));
    return count != null && count == 0;
  }

  public int updateActivity(UUID id, ActivityInput input) {
    return jdbcTemplate.update(
        """
        UPDATE activity
        SET title = ?, starts_at = ?, ends_at = ?, location = ?, responsible = ?,
            capacity = ?, amount_minor = ?, currency = ?, version = version + 1
        WHERE id = ? AND (capacity_frozen = false OR capacity = ?)
        """,
        input.title(),
        timestamp(input.startsAt()),
        timestamp(input.endsAt()),
        input.location(),
        input.responsible(),
        input.capacity(),
        input.price().amountMinor(),
        input.price().currency(),
        id,
        input.capacity());
  }

  public void freezeCapacity(UUID activityId) {
    jdbcTemplate.update(
        "UPDATE activity SET capacity_frozen = true, version = version + 1 "
            + "WHERE id = ? AND capacity_frozen = false",
        activityId);
  }

  public Optional<SnapshotRow> findSnapshot(UUID activityId) {
    return queryOne(
        """
        SELECT snapshot_id, snapshot_version, frozen_capacity
        FROM admission_snapshot WHERE activity_id = ?
        """,
        (rs, rowNum) ->
            new SnapshotRow(
                rs.getObject("snapshot_id", UUID.class),
                rs.getLong("snapshot_version"),
                rs.getInt("frozen_capacity")),
        activityId);
  }

  public SnapshotRow createSnapshot(UUID activityId, long version, int capacity) {
    SnapshotRow snapshot = new SnapshotRow(UUID.randomUUID(), version, capacity);
    jdbcTemplate.update(
        """
        INSERT INTO admission_snapshot (
          activity_id, snapshot_id, snapshot_version, frozen_capacity
        ) VALUES (?, ?, ?, ?)
        """,
        activityId,
        snapshot.snapshotId(),
        snapshot.snapshotVersion(),
        snapshot.frozenCapacity());
    return snapshot;
  }

  private <T> Optional<T> queryOne(String sql, RowMapper<T> mapper, Object... args) {
    return jdbcTemplate.query(sql, mapper, args).stream().findFirst();
  }

  private static Timestamp timestamp(Instant instant) {
    return Timestamp.from(instant);
  }

  private static final RowMapper<EventView> EVENT_MAPPER =
      (rs, rowNum) ->
          new EventView(
              uuid(rs, "id"),
              uuid(rs, "organization_id"),
              rs.getString("title"),
              rs.getString("description"),
              instant(rs, "starts_at"),
              instant(rs, "ends_at"),
              rs.getString("status"),
              rs.getLong("version"));

  private static final RowMapper<ActivityView> ACTIVITY_MAPPER =
      (rs, rowNum) ->
          new ActivityView(
              uuid(rs, "id"),
              uuid(rs, "event_id"),
              uuid(rs, "organization_id"),
              rs.getString("title"),
              instant(rs, "starts_at"),
              instant(rs, "ends_at"),
              rs.getString("location"),
              rs.getString("responsible"),
              rs.getInt("capacity"),
              new MoneyView(rs.getLong("amount_minor"), rs.getString("currency")),
              rs.getLong("version"),
              rs.getBoolean("capacity_frozen"));

  private static UUID uuid(ResultSet resultSet, String column) throws SQLException {
    return resultSet.getObject(column, UUID.class);
  }

  private static Instant instant(ResultSet resultSet, String column) throws SQLException {
    return resultSet.getTimestamp(column).toInstant();
  }

  record SnapshotRow(UUID snapshotId, long snapshotVersion, int frozenCapacity) {}
}

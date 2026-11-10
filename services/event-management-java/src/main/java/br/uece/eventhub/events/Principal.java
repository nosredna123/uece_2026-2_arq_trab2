package br.uece.eventhub.events;

import java.util.UUID;

public record Principal(UUID subjectId, Role role, UUID organizationId) {
  public enum Role {
    PARTICIPANT,
    ORGANIZER,
    GLOBAL_ADMIN
  }
}

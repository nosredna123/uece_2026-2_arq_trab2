package br.uece.eventhub.registration;

import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
class RegistrationExpiryScheduler {
  private final RegistrationService service;

  RegistrationExpiryScheduler(RegistrationService service) {
    this.service = service;
  }

  @Scheduled(fixedDelay = 30_000)
  void expireOffers() {
    service.expireOffers();
  }

  @Scheduled(fixedDelay = 30_000)
  void expireReservations() {
    service.expireReservations();
  }
}

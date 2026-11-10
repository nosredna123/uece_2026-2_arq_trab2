package br.uece.eventhub.registration;

final class CapacityPolicy {
  int allocate(int capacity, int occupied) {
    validate(capacity, occupied);
    if (occupied == capacity) {
      throw new CapacityFullException();
    }
    return occupied + 1;
  }

  int release(int capacity, int occupied) {
    validate(capacity, occupied);
    if (occupied == 0) {
      throw new IllegalStateException("Cannot release an unoccupied capacity slot.");
    }
    return occupied - 1;
  }

  private void validate(int capacity, int occupied) {
    if (capacity < 1 || occupied < 0 || occupied > capacity) {
      throw new IllegalStateException("Activity ledger capacity invariant is violated.");
    }
  }
}

final class CapacityFullException extends RuntimeException {
  CapacityFullException() {
    super("The activity has no available capacity.");
  }
}

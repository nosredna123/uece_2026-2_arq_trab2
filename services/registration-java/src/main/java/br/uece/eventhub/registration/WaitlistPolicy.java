package br.uece.eventhub.registration;

final class WaitlistPolicy {
  long nextSequence(long currentHighest) {
    if (currentHighest < 0 || currentHighest == Long.MAX_VALUE) {
      throw new IllegalStateException("Waitlist sequence cannot be advanced.");
    }
    return currentHighest + 1;
  }
}

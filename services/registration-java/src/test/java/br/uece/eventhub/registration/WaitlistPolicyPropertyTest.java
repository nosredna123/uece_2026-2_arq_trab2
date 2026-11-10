package br.uece.eventhub.registration;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import net.jqwik.api.ForAll;
import net.jqwik.api.Property;
import net.jqwik.api.constraints.IntRange;
import org.junit.jupiter.api.Test;

class WaitlistPolicyPropertyTest {
  private final WaitlistPolicy policy = new WaitlistPolicy();

  @Property
  void assignedSequenceIsStrictlyIncreasing(
      @ForAll @IntRange(min = 0, max = 1000000) int highestAssigned,
      @ForAll @IntRange(min = 1, max = 1000) int additionalEntries) {
    long sequence = highestAssigned;

    for (int index = 0; index < additionalEntries; index++) {
      long next = policy.nextSequence(sequence);
      assertThat(next).isGreaterThan(sequence);
      sequence = next;
    }

    assertThat(sequence).isEqualTo((long) highestAssigned + additionalEntries);
  }

  @Test
  void rejectsInvalidOrExhaustedSequences() {
    assertThatThrownBy(() -> policy.nextSequence(-1))
        .isInstanceOf(IllegalStateException.class)
        .hasMessage("Waitlist sequence cannot be advanced.");
    assertThatThrownBy(() -> policy.nextSequence(Long.MAX_VALUE))
        .isInstanceOf(IllegalStateException.class)
        .hasMessage("Waitlist sequence cannot be advanced.");
  }
}

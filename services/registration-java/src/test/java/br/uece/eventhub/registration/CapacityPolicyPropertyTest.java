package br.uece.eventhub.registration;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import net.jqwik.api.ForAll;
import net.jqwik.api.Property;
import net.jqwik.api.constraints.IntRange;
import org.junit.jupiter.api.Test;

class CapacityPolicyPropertyTest {
  private final CapacityPolicy policy = new CapacityPolicy();

  @Property
  void allocationNeverExceedsFrozenCapacity(
      @ForAll @IntRange(min = 1, max = 1000) int capacity,
      @ForAll @IntRange(min = 0, max = 100) int occupancyPercent) {
    int occupied = (int) ((long) capacity * occupancyPercent / 100);

    if (occupied < capacity) {
      assertThat(policy.allocate(capacity, occupied)).isEqualTo(occupied + 1);
      assertThat(policy.allocate(capacity, occupied)).isLessThanOrEqualTo(capacity);
    } else {
      assertThatThrownBy(() -> policy.allocate(capacity, occupied))
          .isInstanceOf(CapacityFullException.class);
    }
  }

  @Property
  void releaseNeverProducesNegativeOccupancy(
      @ForAll @IntRange(min = 1, max = 1000) int capacity,
      @ForAll @IntRange(min = 1, max = 100) int occupancyPercent) {
    int occupied = Math.max(1, (int) ((long) capacity * occupancyPercent / 100));

    assertThat(policy.release(capacity, occupied)).isBetween(0, capacity - 1);
  }

  @Property
  void rejectsInvalidLedgerStates(
      @ForAll @IntRange(min = -100, max = 0) int invalidCapacity,
      @ForAll @IntRange(min = 0, max = 100) int occupied) {
    assertThatThrownBy(() -> policy.allocate(invalidCapacity, occupied))
        .isInstanceOf(IllegalStateException.class)
        .hasMessage("Activity ledger capacity invariant is violated.");
    assertThatThrownBy(() -> policy.release(invalidCapacity, occupied))
        .isInstanceOf(IllegalStateException.class)
        .hasMessage("Activity ledger capacity invariant is violated.");
  }

  @Property
  void rejectsOccupancyOutsideCapacity(
      @ForAll @IntRange(min = 1, max = 1000) int capacity,
      @ForAll @IntRange(min = 1, max = 1000) int excess) {
    int occupied = capacity + excess;

    assertThatThrownBy(() -> policy.allocate(capacity, occupied))
        .isInstanceOf(IllegalStateException.class)
        .hasMessage("Activity ledger capacity invariant is violated.");
    assertThatThrownBy(() -> policy.release(capacity, occupied))
        .isInstanceOf(IllegalStateException.class)
        .hasMessage("Activity ledger capacity invariant is violated.");
  }

  @Test
  void rejectsNegativeOccupancy() {
    assertThatThrownBy(() -> policy.allocate(1, -1))
        .isInstanceOf(IllegalStateException.class)
        .hasMessage("Activity ledger capacity invariant is violated.");
    assertThatThrownBy(() -> policy.release(1, -1))
        .isInstanceOf(IllegalStateException.class)
        .hasMessage("Activity ledger capacity invariant is violated.");
  }

  @Test
  void rejectsReleasingAnEmptyCapacitySlot() {
    assertThatThrownBy(() -> policy.release(1, 0))
        .isInstanceOf(IllegalStateException.class)
        .hasMessage("Cannot release an unoccupied capacity slot.");
  }
}

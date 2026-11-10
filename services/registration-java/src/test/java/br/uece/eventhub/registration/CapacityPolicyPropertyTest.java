package br.uece.eventhub.registration;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import net.jqwik.api.ForAll;
import net.jqwik.api.Property;
import net.jqwik.api.constraints.IntRange;

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
}

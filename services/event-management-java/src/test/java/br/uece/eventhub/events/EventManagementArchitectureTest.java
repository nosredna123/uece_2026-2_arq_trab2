package br.uece.eventhub.events;

import static com.tngtech.archunit.lang.syntax.ArchRuleDefinition.noClasses;

import com.tngtech.archunit.core.importer.ImportOption;
import com.tngtech.archunit.junit.AnalyzeClasses;
import com.tngtech.archunit.junit.ArchTest;
import com.tngtech.archunit.lang.ArchRule;

@AnalyzeClasses(packages = "br.uece.eventhub", importOptions = ImportOption.DoNotIncludeTests.class)
class EventManagementArchitectureTest {
  @ArchTest
  static final ArchRule eventManagementDoesNotDependOnOtherDomains =
      noClasses()
          .that()
          .resideInAPackage("br.uece.eventhub.events..")
          .should()
          .dependOnClassesThat()
          .resideInAnyPackage(
              "br.uece.eventhub.registration..",
              "br.uece.eventhub.attendance..",
              "br.uece.eventhub.reporting..",
              "br.uece.eventhub.identity..",
              "br.uece.eventhub.billing..",
              "br.uece.eventhub.notification..");
}

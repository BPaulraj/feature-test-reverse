@trading @orders
Feature: Cancel open orders

  Background:
    Given the trader is logged in as "TRD1001"
    And the trader has an open limit order "ORD-5001" to BUY 20 "INFY" at 18.50

  @TC_48213 @smoke
  Scenario: Cancel an open order
    When the trader cancels order "ORD-5001"
    Then the order status is "CANCELLED"
    And the reserved funds are released

  Scenario: Cancel confirmation dialog can be dismissed
    When the trader clicks cancel on order "ORD-5001"
    And the trader dismisses the confirmation dialog

  Scenario: A filled order cannot be cancelled
    Given order "ORD-5001" has been filled
    When the trader tries to cancel order "ORD-5001"
    Then the error message "Order already filled" is displayed

@market-data
Feature: Watchlist

  Background:
    Given the trader is logged in as "TRD1001"

  Scenario: Add a symbol to the watchlist
    When the trader adds "AAPL" to the watchlist
    Then "AAPL" is shown in the watchlist with its last traded price

  Scenario: Add a symbol to the watchlist
    When the trader adds "aapl" in lower case to the watchlist
    Then "AAPL" is shown in the watchlist with its last traded price

  Scenario Outline: Price alert for <symbol>
    When the trader sets a price alert for "<symbol>" at <target>
    And the price of "<symbol>" reaches <target>
    Then a notification "<symbol> reached <target>" is shown

    Examples:
      | symbol | target |
      | AAPL   | 155.00 |

  Scenario Outline: Watchlist sorting by <column>
    When the trader sorts the watchlist by "<column>" in <direction>order
    Then the watchlist is sorted by "<column>" <direction>

    Examples:
      | column | direction |
      | Change | desc      |

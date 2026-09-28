@portfolio
Feature: Portfolio holdings

  Background:
    Given the trader is logged in as "TRD1001"

  Scenario: Holdings table shows current positions
    Given the trader holds the following positions:
      | Symbol | Quantity | Avg Price |
      | AAPL   | 10       | 145.20    |
      | MSFT   | 5        | 310.00    |
    When the trader opens the holdings page
    Then the holdings table lists 2 positions
    And the total market value is shown in "USD"

  Scenario Outline: Unrealised P&L for <symbol>
    Given the trader holds <quantity> shares of "<symbol>" bought at <avg price>
    And the last traded price of "<symbol>" is <ltp>
    When the trader opens the holdings page
    Then the unrealised P&L for "<symbol>" is "<pnl>"

    Examples: gains
      | symbol | quantity | avg price | ltp    | pnl     |
      | AAPL   | 10       | 145.20    | 150.20 | +50.00  |

    Examples: losses and flat
      | symbol | quantity | avg price | ltp    | pnl     | account-id |
      | MSFT   | 5        | 310.00    | 300.00 | -50.00  | ACC-01     |
      | INFY   | 20       | 18.50     | 18.50  | 0.00    | ACC-02     |

  Scenario Outline: Export holdings as <format>
    When the trader exports holdings for account "<accountId>" as "<format>"
    Then a "<format>" file named "<file name>" is downloaded

    Examples:
      | accountId | format | file name         |
      | ACC-01    | CSV    | holdings_ACC-01.csv |
      | ACC-01    | PDF    |                   |

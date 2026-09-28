@auth @P1
Feature: Trader login
  Traders sign in to the StockBroker web platform with their client ID and password.
  Accounts are locked after 3 failed attempts.

  Background:
    Given the trader is on the StockBroker login page

  @smoke
  Scenario: Successful login with valid credentials
    When the trader logs in with client ID "TRD1001" and password "Secure#2024"
    Then the trading dashboard is displayed
    And the header shows "Welcome, TRD1001"

  Scenario Outline: Login is rejected for <reason>
    When the trader logs in with client ID "<clientId>" and password "<password>"
    Then the error message "<error>" is displayed
    And the trader remains on the login page

    @negative
    Examples: invalid credentials
      | reason         | clientId | password    | error                          |
      | wrong password | TRD1001  | wrong       | Invalid client ID or password  |
      | unknown client | TRD9999  | Secure#2024 | Invalid client ID or password  |

    @negative @security
    Examples: locked account
      | reason         | clientId | password    | error                                  |
      | locked account | TRD2002  | Secure#2024 | Account locked. Contact support@broker.example |

  Scenario: Forgot password sends a reset link
    When the trader requests a password reset for "trader.one@broker.example"
    Then a reset link is sent to "trader.one@broker.example"

  # Scenario: Login with biometrics
  #   When the trader logs in with fingerprint
  #   Then the trading dashboard is displayed

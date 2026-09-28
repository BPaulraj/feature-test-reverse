Feature: Market quotes

  Scenario: Quote endpoint returns the last traded price
    When a GET request is sent to "/v1/quotes/AAPL"
    Then the response status code is 200
    And the response body field "lastPrice" is a positive number

  Scenario: Price alert created via API is shown in the web watchlist
    Given the trader is logged in as "TRD1001"
    And a POST request is sent to "/v1/alerts" with body:
      """json
      { "symbol": "AAPL", "target": 155.00 }
      """
    When the trader opens the watchlist page
    Then the alert for "AAPL" at 155.00 is displayed

@api @orders
Feature: Orders REST API
  The Orders API (v1) lets partner systems place and query equity orders.

  Background:
    Given the API base URL is "https://api.stockbroker.test"
    And the request header "Authorization" is "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJUUkQxMDAxIn0.dGVzdHNpZ25hdHVyZQ"
    And the request header "Content-Type" is "application/json"

  @smoke
  Scenario: Create a market order
    When a POST request is sent to "/v1/orders" with body:
      """json
      {
        "clientId": "TRD1001",
        "symbol": "AAPL",
        "side": "BUY",
        "type": "MARKET",
        "quantity": 10
      }
      """
    Then the response status code is 201
    And the response body field "status" is "FILLED"
    And the response matches the schema "schemas/order-response.json"

  Scenario Outline: Order validation returns <statusCode> for <case>
    When a POST request is sent to "/v1/orders" with body:
      """json
      { "clientId": "TRD1001", "symbol": "<symbol>", "side": "BUY", "type": "MARKET", "quantity": <qty> }
      """
    Then the response status code is <statusCode>
    And the response body field "error.code" is "<errorCode>"

    Examples:
      | case           | symbol | qty | statusCode | errorCode        |
      | unknown symbol | ZZZZ   | 10  | 400        | UNKNOWN_SYMBOL   |
      | zero quantity  | AAPL   | 0   | 400        | INVALID_QUANTITY |
      | market closed  | AAPL   | 10  | 409        | MARKET_CLOSED    |

  Scenario: Get order by ID
    When a GET request is sent to "/v1/orders/ORD-5001" with query parameters:
      | include | fills |
    Then the response status code is 200
    And the response body contains:
      | field   | value    |
      | orderId | ORD-5001 |
      | status  | OPEN     |
    And the response time is below 500 ms

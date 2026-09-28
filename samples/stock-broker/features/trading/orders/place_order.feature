@trading @orders
Feature: Place equity orders
  Traders place buy and sell orders for listed equities during market hours.

  Background:
    Given the trader is logged in as "TRD1001"
    And the market is open

  Rule: Market orders execute immediately at the best available price

    Background:
      Given the trader has available funds of "50000.00" USD

    @smoke @P1
    Scenario Outline: Place a market <side> order for <symbol>
      When the trader places a market "<side>" order for <qty> shares of "<symbol>"
      Then the order status is "<status>"
      And the confirmation shows "<qty>x <symbol>"
      And the order appears in the order book

      Examples:
        | side | symbol | qty | status | comment                  |
        | BUY  | AAPL   | 10  | FILLED | blue chip                |
        | SELL | MSFT   | 5   | FILLED | sell from holdings       |
        | BUY  | TSLA   | 1   | FILLED | single share edge case   |

    Scenario: Market order is rejected when funds are insufficient
      When the trader places a market "BUY" order for 1000 shares of "AAPL"
      Then the order is rejected with message "Insufficient funds"
      But no order appears in the order book

  Rule: Limit orders wait until the price reaches the limit

    @P2
    Scenario Outline: Place a limit order with <order type> validity
      When the trader enters the order details:
        | Symbol   | <symbol>      |
        | Side     | BUY           |
        | Quantity | <qty>         |
        | Limit    | <limit price> |
        | Validity | <order type>  |
      And the trader submits the order
      Then the order status is "OPEN"
      And the order shows validity "<order type>"

      Examples:
        | symbol | qty | limit price | order type |
        | INFY   | 20  | 18.50       | DAY        |
        | AAPL   | 5   | 150.00      | GTC        |

    Scenario: Limit order ticket preview
      When the trader previews a limit order ticket
      Then the ticket preview shows:
        """
        Order type : LIMIT
        Validity   : DAY
        Fees       : 0.1% (min 1.00 USD)
        """

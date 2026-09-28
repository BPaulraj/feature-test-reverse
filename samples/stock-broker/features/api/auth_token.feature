Feature: Partner authentication API

  Scenario Outline: Token request for partner <partner>
    When a POST request is sent to "/v1/auth/token" with form data:
      | client_id     | <clientId>         |
      | client_secret | <clientSecret>     |
      | grant_type    | client_credentials |
    Then the response status code is <status>

    Examples:
      | partner | clientId     | clientSecret         | status |
      | Acme    | acme-partner | s3cr3t-acme-2024-xyz | 200    |
      | Unknown | nobody       | wrong-secret-000     | 401    |

  Scenario: Legacy trade confirmation in XML
    Given the request header "x-api-key" is "k9f8e7d6c5b4a3"
    When a POST request is sent to "/v1/legacy/confirmations" with body:
      """xml
      <confirmation><orderId>ORD-5001</orderId><status>FILLED</status></confirmation>
      """
    Then the response status code is 202

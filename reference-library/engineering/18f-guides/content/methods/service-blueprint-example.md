---
title: "Service blueprint example"
source: 18F Guides
source_url: https://github.com/18F/guides
licence: CC0-1.0
domain: engineering
subdomain: 18f-guides
date_added: 2026-04-25
---

<style type="text/css" media="print">
@page {
  margin: 1in;
}
</style>


  <section class="category category--decide usa-section usa-prose">
    <section class="example-card grid-container">
      [Back to service blueprint card]({{ )

      <h1>Service blueprint example</h1>
      

        This simple [service blueprint]({{ "/methods/decide/service-blueprint/" | url }}) is an example of how you might lay out the layers of your service. There are many ways to create a service blueprint and adapt the structure to best represent and capture your context.
      
        <p class="caption">This is a table showing an example service blueprint. The top row is the journey of a customer ordering and eating at a fast food restaurant. Each column is a different step in the customer's journey. The remaining rows describe different parts of the service and the support provided at each step in the customer’s journey.</p>

      
      
    <table class="usa-table service-blueprint">
        <tr>
          <th scope="row" class="role">

          {% image "assets/methods/img/18f-icons/user-check.svg" %}

          User steps
          The primary action someone takes when interacting with the service</th>
          <th scope="col" class="steps">Decide and look up how to get to the restaurant</th>
          <th scope="col" class="steps">Arrive at the restaurant and order</th>
          <th scope="col" class="steps">Find a table and eat the food</th>
          <th scope="col" class="steps">Clean up and leave the restaurant</th>
        </tr>
        <tr>
          <th scope="row">

          {% image "assets/methods/img/18f-icons/user-network--c.svg" %}

          Frontstage
          The online and offline interactions that users have with the service which includes the people, places, objects which users interact with</th>
          <td>
            
              <li>Website</li>
              <li>Newspaper</li>
              <li>Radio ad</li>
              <li>Address search</li>
            
          </td>
        <td>
          
            <li>Parking Lot</li>
            <li>Lobby</li>
            <li>Signage</li>
            <li>Electronic menu</li>
            <li>Counter</li>
            <li>Cash register</li>
            <li>Card reader</li>
          
        </td>
        <td>
          
            <li>Paper goods</li>
            <li>Condiment counter</li>
            <li>Condiment containers</li>
            <li>Napkins</li>
            <li>Cutlery</li>
          
        </td>
        <td>
          
            <li>Trash bin</li>
            <li>Tray return</li>
          
        </td>
        </tr>
        <tr>
          <th scope="row">

          {% image "assets/methods/img/18f-icons/settings.svg" %}

          Backstage
        Activities in the systems and
        processes that support the
        frontstage experience, but are not
        visible to users</th>
          <td>
            
              <li>
                Website maintenance
                <li>Customer service support and staffing</li>
                <li>Branding and advertising activities</li>
              </li>
            
          </td>
          <td>
            
              <li>Staff support and scheduling</li>
              <li>Payment system</li>
              <li>Menu upkeep</li>
            
          </td>
          <td>
            
              <li>Staff support and scheduling</li>
              <li>Dining area maintenance</li>
            
          </td>
          <td>
          
              <li>Staff support and scheduling</li>
              <li>Waste collection</li>
          
          </td>
        </tr>
        <tr>
          <th scope="row">

          {% image "assets/methods/img/18f-icons/list-to-do-checked--c.svg" %}

          Support processes
          Activities executed by the rest of the organization or external partners — such as ongoing data management or software licensing — that don’t fall into the other rows</th>
          <td>
            
              <li>Advertising partnerships</li>
              <li>Website server support</li>
              <li>Branding and website efforts</li>
            
          </td>
          <td>
            
              <li>Building license</li>
              <li>Food supply</li>
              <li>Gas, electricity, and water</li>
              <li>Food safety regulations</li>
              <li>Workers' union partnership</li>
              <li>Employee policies</li>
            
          </td>
          <td>
            
              <li>Cutlery supply</li>
              <li>Cleaning supply</li>
              <li>Furniture layout design</li>
            
          </td>
          <td>
            
              <li>Waste management contracts</li>
              <li>Customer service staff support and scheduling</li>
            
          </td>
        </tr>
    </table>
  
    </section>
  </section>

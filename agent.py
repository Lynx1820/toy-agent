import hydra
from omegaconf import DictConfig
from duffel_api import Duffel
from openai import OpenAI
import json 
from datetime import date

OPENAI_DEFAULT_MODEL = "gpt-3.5-turbo"
OPENAI_MAX_OUTPUT_TOKENS = 4096

"""
Note: GPT-3 was giving the wrong departure date, so today's date had to be provided. I read about funciton calling as an alternative way to parse responses from models, but prompting seems to work well enough.  
"""
prompt_template = f"""

Today's date is {date.today()}
Provide your response in the following JSON format:
origin: {{ 3-letter IATA code }}
destination: {{ 3-letter IATA code }}
departure_date: {{ year-month-day }}
change_departure_date: {{ True if they want to change their previous departure_date and False if they want to book a new reservation. }}
"""

def get_passenger_info(cfg):
  test_person = cfg.test_person
  return [
        {
            "phone_number": test_person.phone_number,
            "email": test_person.email,
            "title": test_person.title,
            "gender": test_person.gender,
            "family_name": test_person.family_name,
            "given_name": test_person.given_name,
            "born_on": test_person.born_on,
            "type": test_person.type,
        }
    ]
def get_payment_info(total_currency, total_amount): 
 return [
      {
          "currency": total_currency,
          "amount": total_amount,
          "type": "balance",
      }
  ]
def search_and_book_offer_request(cfg, duffel_client, origin, destination, departure_date): 
  slices = [
    {
      "origin": origin,
      "destination": destination,
      "departure_date": departure_date,
    }
  ]
  passengers = get_passenger_info(cfg)
  response = duffel_client.offer_requests.create() \
                      .slices(slices) \
                      .passengers([{"type": "adult"}]) \
                      .return_offers() \
                      .execute()
  offer = response.offers[0]
  duffel_client.offers.get(offer.id)

  ## TODO: Ask for confirmation
  print(
    f"Making an order for the best (cheapest) option with {offer.owner.name} flight departing at "
    + f"{offer.slices[0].segments[0].departing_at} "
    + f"{offer.total_amount} {offer.total_currency}"
  )

  passengers[0]['id'] = offer.passengers[0].id
  payments = get_payment_info(offer.total_currency, offer.total_amount)
  order = (
    duffel_client.orders.create()
    .payments(payments)
    .passengers(passengers)
    .selected_offers([offer.id])
    .execute()
  )
  return order
  
def change_request(duffel_client, order, origin, destination, departure_date): 
  order_change_request_slices = {
        "add": [
            {
              "origin": origin,
              "destination": destination,
              "departure_date": departure_date,
              "cabin_class": "economy",
            }
        ],
        "remove": [
            {
                "slice_id": order.slices[0].id,
            }
        ],
    }
    
  order_change_request = (
      duffel_client.order_change_requests.create(order.id)
      .slices(order_change_request_slices)
      .execute()
  )

  order_change_offers = duffel_client.order_change_offers.list(order_change_request.id)
  order_change_offers_list = list(enumerate(order_change_offers))

  print(f"Got {len(order_change_offers_list)} options for changing the order; picking first option")
  
  ## TODO: Handle errors - on some orders, there is an Internal Server Error or an error saying the order cannot be changed.
  order_change = duffel_client.order_changes.create(order_change_offers_list[0][1].id)

  print(f"Created order change {order_change.id}, confirming...")

  payment = get_payment_info(order_change.change_total_currency, order_change.change_total_amount)[0]

  duffel_client.order_changes.confirm(order_change.id, payment)

  print(f"Processed change to order {order.id} costing {order_change.change_total_amount} ({order_change.change_total_currency})")

def load_json(content): 
  try:
    request = json.loads(content)
    return True, request
  except ValueError as e:
    return False, content

@hydra.main(config_path="configs", config_name="params")
def main(cfg: DictConfig):
    DUFFEL_API_KEY = cfg.duffel_api_key
    OPENAI_API_KEY = cfg.openai_api_key
    
    """
    Task 1: convert natural language from user to Duffel Query
    Task 2: execute Duffel query 
    Task 3: convert Duffel response to natural text
    """
    exit_conditions = (":q", "quit", "exit")
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
    duffel_client = Duffel(access_token=DUFFEL_API_KEY)
    
    # TODO: Handle multiple requests
    order = None
    while True:
        query = input("> ")
        if query in exit_conditions:
            break
        else:
            response = openai_client.chat.completions.create(model=OPENAI_DEFAULT_MODEL,
                                        messages=[
                                        {"role": "system", "content": prompt_template},
                                        {"role": "user", "content": query}
                                        ],
                                        max_tokens=OPENAI_MAX_OUTPUT_TOKENS,
                                        temperature=0,
                                        top_p=1)
            content = response.choices[0].message.content.strip('\n')
            is_valid_json, json_request = load_json(content) 
            
            ## TODO Handle invalid request
            if not is_valid_json: 
              print(content)
              continue
            origin, destination, departure_date, update_departure_date = json_request['origin'], json_request['destination'], json_request['departure_date'], json_request['change_departure_date']
            if update_departure_date: 
              change_request(duffel_client, order, origin, destination, departure_date)
            else: 
              order = search_and_book_offer_request(cfg, duffel_client, origin, destination, departure_date)
            print(f"\n🎉 Created order {order.id} with booking reference: {order.booking_reference}")
main()
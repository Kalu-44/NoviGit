import asyncio
import json
import websockets
from dataclasses import dataclass, asdict, field
from typing import Optional, List, Dict, Tuple, Any

InstrumentID_t = str
Price_t = int
Time_t = int
Quantity_t = int
OrderID_t = str
TeamID_t = str

@dataclass
class BaseMessage:
    type: str

@dataclass
class AddOrderRequest(BaseMessage):
    type: str = field(default="add_order", init=False)
    user_request_id: str
    instrument_id: InstrumentID_t
    price: Price_t
    expiry: Time_t
    side: str
    quantity: Quantity_t

@dataclass
class CancelOrderRequest(BaseMessage):
    type: str = field(default="cancel_order", init=False)
    user_request_id: str
    order_id: OrderID_t
    instrument_id: InstrumentID_t

@dataclass
class GetInventoryRequest(BaseMessage):
    type: str = field(default="get_inventory", init=False)
    user_request_id: str

@dataclass
class GetPendingOrdersRequest(BaseMessage):
    type: str = field(default="get_pending_orders", init=False)
    user_request_id: str

@dataclass
class WelcomeMessage(BaseMessage):
    type: str
    message: str

@dataclass
class AddOrderResponseData:
    order_id: Optional[OrderID_t] = None
    message: Optional[str] = None
    immediate_inventory_change: Optional[Quantity_t] = None
    immediate_balance_change: Optional[Quantity_t] = None

@dataclass
class AddOrderResponse(BaseMessage):
    type: str
    user_request_id: str
    success: bool
    data: AddOrderResponseData

@dataclass
class CancelOrderResponse(BaseMessage):
    type: str
    user_request_id: str
    success: bool
    message: Optional[str] = None

@dataclass
class ErrorResponse(BaseMessage):
    type: str
    user_request_id: str
    message: str

@dataclass
class GetInventoryResponse(BaseMessage):
    type: str
    user_request_id: str
    data: Dict[InstrumentID_t, Tuple[Quantity_t, Quantity_t]]

@dataclass
class OrderJSON:
    orderID: OrderID_t
    teamID: TeamID_t
    price: Price_t
    time: Time_t
    expiry: Time_t
    side: str
    unfilled_quantity: Quantity_t
    total_quantity: Quantity_t
    live: bool

@dataclass
class GetPendingOrdersResponse:
    type: str
    user_request_id: str
    data: Dict[InstrumentID_t, Tuple[List[OrderJSON], List[OrderJSON]]]

@dataclass
class OrderbookDepth:
    bids: Dict[Price_t, Quantity_t]
    asks: Dict[Price_t, Quantity_t]

@dataclass
class CandleDataResponse:
    tradeable: Dict[InstrumentID_t, List[Dict[str, Any]]]
    untradeable: Dict[InstrumentID_t, List[Dict[str, Any]]]

Trade_t = Dict[str, Any]
Settlement_t = Dict[str, Any]
Cancel_t = Dict[str, Any]

@dataclass
class MarketDataResponse(BaseMessage):
    type: str
    time: Time_t
    candles: CandleDataResponse
    orderbook_depths: Dict[InstrumentID_t, OrderbookDepth]
    events: List[Dict[str, Any]]
    user_request_id: Optional[str] = None

global_user_request_id = 0

class DemoTradingBot:
    def __init__(self, uri: str, team_secret: str, print_market_data: bool = True):
        self.uri = f"{uri}?team_secret={team_secret}"
        self.ws = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._trade_sequence_triggered = False
        self.print_market_data = print_market_data
        self._instrument_ids = set()

    async def connect(self):
        self.ws = await websockets.connect(self.uri)
        welcome_data = json.loads(await self.ws.recv())
        welcome_message = WelcomeMessage(**welcome_data)
        
        print(json.dumps({"welcome": asdict(welcome_message)}, indent=2))
        asyncio.create_task(self._receive_loop())

    async def _receive_loop(self):
        assert self.ws, "Websocket connection not established."
        async for msg in self.ws:
            data = json.loads(msg)

            if not (data.get("type") == "market_data_update" and not self.print_market_data):
                print(json.dumps({"message": data}, indent=2))

            rid = data.get("user_request_id")
            if rid and rid in self._pending:
                self._pending[rid].set_result(data)
                del self._pending[rid]

            msg_type = data.get("type")
            if msg_type == "market_data_update":
                try:
                    parsed_orderbook_depths = {
                        instr_id: OrderbookDepth(**depth_data)
                        for instr_id, depth_data in data.get("orderbook_depths", {}).items()
                    }
                    parsed_candles = CandleDataResponse(**data.get("candles", {}))

                    market_data = MarketDataResponse(
                        type=data["type"],
                        time=data["time"],
                        candles=parsed_candles,
                        orderbook_depths=parsed_orderbook_depths,
                        events=data.get("events", []),
                        user_request_id=data.get("user_request_id")
                    )
                    self._handle_market_data_update(market_data)
                except KeyError as e:
                    print(f"Error: Missing expected key in MarketDataResponse: {e}. Data: {data}")
                except Exception as e:
                    print(f"Error deserializing MarketDataResponse: {e}. Data: {data}")

    def _handle_market_data_update(self, data: MarketDataResponse):
        if self._trade_sequence_triggered:
            return

        for instr, depths in data.orderbook_depths.items():
            if instr in self._instrument_ids:
                continue

            if instr.startswith("$CARD"):
                asks = list(depths.asks.items())
                if asks:
                    price = int(asks[0][0])
                else:
                    continue

                try:
                    expiry = int(instr.split("_")[-1])
                except ValueError:
                    print(f"Warning: Could not parse expiry from instrument ID: {instr}")
                    continue

                print(json.dumps({
                    "new_instrument_to_trade": instr,
                    "best_ask_price": price,
                    "expiry": expiry}, indent=2))

                self._trade_sequence_triggered = True
                self._instrument_ids.add(instr)
                
                asyncio.create_task(self.run_sequence(instr, price))
                return

    async def send(self, payload: BaseMessage, timeout: int = 3):
        global global_user_request_id
        rid = str(global_user_request_id).zfill(10)
        global_user_request_id += 1

        payload.user_request_id = rid
        payload_dict = asdict(payload)

        fut = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut

        await self.ws.send(json.dumps(payload_dict))
        print(json.dumps({"sent": payload_dict}, indent=2))

        try:
            resp = await asyncio.wait_for(fut, timeout)
            if resp.get("type") == "add_order_response":
                resp['data'] = AddOrderResponseData(**resp.get('data', {}))
                return AddOrderResponse(**resp)
            elif resp.get("type") == "cancel_order_response":
                return CancelOrderResponse(**resp)
            elif resp.get("type") == "get_inventory_response":
                return GetInventoryResponse(**resp)
            elif resp.get("type") == "get_pending_orders_response":
                parsed_data = {}
                for instr_id, (bids_raw, asks_raw) in resp.get('data', {}).items():
                    parsed_bids = [OrderJSON(**order_data) for order_data in bids_raw]
                    parsed_asks = [OrderJSON(**order_data) for order_data in asks_raw]
                    parsed_data[instr_id] = (parsed_bids, parsed_asks)
                resp['data'] = parsed_data
                return GetPendingOrdersResponse(**resp)
            elif resp.get("type") == "error":
                return ErrorResponse(**resp)
            else:
                return resp
        except asyncio.TimeoutError:
            if rid in self._pending:
                del self._pending[rid]
            print(json.dumps({"error": "timeout", "user_request_id": rid}, indent=2))
            return {"success": False, "user_request_id": rid, "message": "Request timed out"}

    async def buy(self, instr: InstrumentID_t, price: Price_t):
        expiry = int(instr.split("_")[-1]) + 10
        buy_request = AddOrderRequest(
            user_request_id="",
            instrument_id=instr,
            price=price,
            quantity=1,
            side="bid",
            expiry=expiry * 1000
        )
        return await self.send(buy_request)

    async def get_pending_orders(self):
        get_pending_request = GetPendingOrdersRequest(user_request_id="")
        return await self.send(get_pending_request)

    async def get_inventory(self):
        get_inventory_request = GetInventoryRequest(user_request_id="")
        return await self.send(get_inventory_request)

    async def sell(self, instr: InstrumentID_t, price: Price_t):
        expiry = int(instr.split("_")[-1]) + 10
        sell_request = AddOrderRequest(
            user_request_id="",
            instrument_id=instr,
            price=price,
            quantity=1,
            side="ask",
            expiry=expiry * 1000
        )
        return await self.send(sell_request)

    async def cancel(self, instr: InstrumentID_t, oid: OrderID_t):
        cancel_request = CancelOrderRequest(
            user_request_id="",
            order_id=oid,
            instrument_id=instr
        )
        return await self.send(cancel_request)

    async def run_sequence(self, instr: InstrumentID_t, price: Price_t):
        try:
            print(f"\n--- Running trading sequence for {instr} at price {price} ---")

            sell_order_id_to_check: Optional[OrderID_t] = None
            sell_order_successfully_submitted = False

            # --- Buy Order ---
            print("1) Sending Buy Order...")
            buy_resp = await self.buy(instr, price)
            
            if isinstance(buy_resp, AddOrderResponse) and buy_resp.success:
                print(f"   Buy Order SUBMISSION SUCCESS. OrderID: {buy_resp.data.order_id}")
            else:
                error_message = ""
                if isinstance(buy_resp, AddOrderResponse):
                    error_message = f" (Server message: '{buy_resp.data.message}')"
                elif isinstance(buy_resp, ErrorResponse):
                    error_message = f" (Error: '{buy_resp.message}')"
                else:
                    error_message = f" (Timeout or Unexpected response type: {type(buy_resp)})"
                print(f"   Buy Order SUBMISSION FAILED{error_message}. Aborting sequence.")
                return 

            # --- Get Inventory ---
            print("2) Getting Inventory after buy...")
            inventory_resp_after_buy = await self.get_inventory()
            initial_instrument_owned_quantity = 0
            initial_instrument_reserved_quantity = 0

            if isinstance(inventory_resp_after_buy, GetInventoryResponse):
                # Corrected: first element is reserved, second is owned
                reserved, owned = inventory_resp_after_buy.data.get(instr, (0, 0))
                initial_instrument_reserved_quantity = reserved
                initial_instrument_owned_quantity = owned
                print(f"   Current Inventory for {instr}: {initial_instrument_owned_quantity} owned, {initial_instrument_reserved_quantity} reserved.")
            else:
                error_message = ""
                if isinstance(inventory_resp_after_buy, ErrorResponse):
                    error_message = f" (Error: '{inventory_resp_after_buy.message}')"
                else:
                    error_message = f" (Timeout or Unexpected response type: {type(inventory_resp_after_buy)})"
                print(f"   Get Inventory failed{error_message}. Aborting sequence.")
                return

            if initial_instrument_owned_quantity <= 0:
                print(f"   Warning: Did not acquire {instr} after buy order (owned quantity is 0). Skipping sell attempts.")
                return

            # --- Sell Order ---
            sell_price = int(price * 1.01)
            print(f"3) Sending Sell Order at {sell_price}...")
            sell_resp = await self.sell(instr, sell_price)

            if isinstance(sell_resp, AddOrderResponse) and sell_resp.success:
                sell_order_id_to_check = sell_resp.data.order_id
                sell_order_successfully_submitted = True
                print(f"   Sell Order SUBMISSION SUCCESS. OrderID: {sell_order_id_to_check}.")
            else:
                error_message = ""
                if isinstance(sell_resp, AddOrderResponse):
                    error_message = f" (Server message: '{sell_resp.data.message}')"
                elif isinstance(sell_resp, ErrorResponse):
                    error_message = f" (Error: '{sell_resp.message}')"
                else:
                    error_message = f" (Timeout or Unexpected response type: {type(sell_resp)})"
                print(f"   Sell Order SUBMISSION FAILED{error_message}. No order was placed to cancel.")
            
            # --- Wait for Sell Order / Cancel if needed ---
            if sell_order_successfully_submitted and sell_order_id_to_check:
                print(f"4) Sell order was successfully submitted. Waiting for it to FILL or TIMEOUT ({5} seconds max), checking pending orders...")
                max_wait_time_for_fill = 5
                poll_interval = 0.5
                waited_time = 0
                order_still_pending = True

                while waited_time < max_wait_time_for_fill and order_still_pending:
                    await asyncio.sleep(poll_interval)
                    waited_time += poll_interval

                    pending_orders_resp = await self.get_pending_orders()
                    if isinstance(pending_orders_resp, GetPendingOrdersResponse):
                        order_found_in_pending = False
                        if instr in pending_orders_resp.data:
                            _, asks = pending_orders_resp.data[instr]
                            for order_json in asks:
                                if order_json.orderID == sell_order_id_to_check:
                                    order_found_in_pending = True
                                    print(f"   [{waited_time:.1f}s] Sell order {sell_order_id_to_check} still LIVE.")
                                    break
                        
                        order_still_pending = order_found_in_pending

                        if not order_still_pending:
                            print(f"   Sell order was successful.")
                            break 
                    else:
                        print(f"   [{waited_time:.1f}s] Failed to get pending orders during poll: {pending_orders_resp}. Cannot confirm if order is pending.")
                
                print(f"5) Final check after {max_wait_time_for_fill}s wait for sell order {sell_order_id_to_check}:")
                print(f"   - Order still pending (based on last poll): {order_still_pending}.")

                if sell_order_successfully_submitted and sell_order_id_to_check and order_still_pending:
                    print(f"   Sell order (ID: {sell_order_id_to_check}) still PENDING after TIMEOUT. Initiating cancellation...")
                    cancel_response = await self.cancel(instr, sell_order_id_to_check)
                    if isinstance(cancel_response, CancelOrderResponse) and cancel_response.success:
                        print(f"      Cancellation SUCCESS. Message: '{cancel_response.message}'.")
                    else:
                        error_message = ""
                        if isinstance(cancel_response, CancelOrderResponse):
                            error_message = f" (Server message: '{cancel_response.message}')"
                        elif isinstance(cancel_response, ErrorResponse):
                            error_message = f" (Error: '{cancel_response.message}')"
                        else:
                            error_message = f" (Timeout or Unexpected response type: {type(cancel_response)})"
                        print(f"      Cancellation FAILED{error_message}.")
                elif sell_order_successfully_submitted and sell_order_id_to_check and not order_still_pending:
                    pass # Message 'Sell order was successful.' already printed within the loop.
                else:
                    print("   Sell order was NOT SUCCESSFULLY SUBMITTED, or no order ID. No cancellation possible or needed.")

            else:
                print("4) Sell order was not submitted successfully. No waiting or cancellation needed.")

            print("--- Trading sequence complete ---")
        except Exception as e:
            print(f"An unexpected error occurred during trading sequence: {e}")
        finally:
            self._trade_sequence_triggered = False

async def main():
    EXCHANGE_URI = "ws://192.168.100.10:9001/trade"
    TEAM_SECRET = "YOUR_TEAM_SECRET"

    bot = DemoTradingBot(
        EXCHANGE_URI,
        TEAM_SECRET,
        print_market_data=False
    )

    await bot.connect()
    await asyncio.Future()

if __name__ == '__main__':
    asyncio.run(main())

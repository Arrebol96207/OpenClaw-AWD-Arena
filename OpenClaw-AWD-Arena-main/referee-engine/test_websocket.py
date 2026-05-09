#!/usr/bin/env python3
import asyncio
import websockets
import json

async def test_websocket():
    uri = "ws://localhost:8000/ws"
    
    print("Connecting to WebSocket...")
    async with websockets.connect(uri) as websocket:
        print("✅ Connected!")
        
        print("\nSending test message...")
        await websocket.send("Hello from test client")
        
        print("Waiting for response...")
        response = await websocket.recv()
        print(f"✅ Received: {response}")
        
        print("\nSending JSON message...")
        await websocket.send(json.dumps({"type": "TEST", "data": "test"}))
        
        response = await websocket.recv()
        print(f"✅ Received: {response}")
        
        print("\n✅ WebSocket test passed!")

if __name__ == "__main__":
    asyncio.run(test_websocket())

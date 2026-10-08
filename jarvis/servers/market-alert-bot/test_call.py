import asyncio
import os
from dotenv import load_dotenv
from telethon import TelegramClient

# Load configuration
load_dotenv(".env", override=True)

API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")
PHONE_A = os.getenv("TELEGRAM_PHONE_A")
PHONE_B = os.getenv("TELEGRAM_PHONE_B")

if not API_ID or not API_HASH or API_ID == "12345678":
    print("❌ ERROR: You must set real TELEGRAM_API_ID and TELEGRAM_API_HASH in .env")
    print("   Get them from https://my.telegram.org using Account A's phone number.")
    exit(1)

async def main():
    print(f"🔄 Initialising Telethon for Account A ({PHONE_A})...")
    
    # This creates the session file and handles the interactive login prompt
    os.makedirs("sessions", exist_ok=True)
    client = TelegramClient('sessions/account_a', int(API_ID), API_HASH)
    
    # start() will automatically prompt you in the terminal for the Telegram login code
    await client.start(phone=PHONE_A)
    print("✅ Login successful! Session saved.")
    
    print(f"\n📞 Attempting to call Account B ({PHONE_B})...")
    try:
        # Resolve Account B's entity
        target = await client.get_entity(PHONE_B)
        
        # Place the call using raw MTProto request to make it ring
        from telethon.tl.functions.phone import RequestCallRequest
        from telethon.tl.types import PhoneCallProtocol
        import random

        protocol = PhoneCallProtocol(
            min_layer=93, 
            max_layer=93, 
            udp_p2p=True, 
            udp_reflector=True, 
            library_versions=["1.0.0"]
        )

        call_req = RequestCallRequest(
            user_id=target,
            random_id=random.randint(0, 0x7fffffff - 1),
            g_a_hash=os.urandom(32),  # Dummy hash just to trigger the ringing phase
            protocol=protocol
        )
        
        call_res = await client(call_req)
        print("🔔 Ringing... (letting it ring for 15 seconds)")
        
        await asyncio.sleep(15)
        
        # Hang up
        from telethon.tl.functions.phone import DiscardCallRequest
        from telethon.tl.types import InputPhoneCall
        from telethon.tl.types import PhoneCallDiscardReasonDisconnect
        
        await client(DiscardCallRequest(
            peer=InputPhoneCall(
                id=call_res.phone_call.id, 
                access_hash=call_res.phone_call.access_hash
            ),
            duration=0,
            reason=PhoneCallDiscardReasonDisconnect(),
            connection_id=0
        ))
        print("✅ Call finished and hung up successfully!")
        
    except Exception as e:
        print(f"\n❌ Call failed: {e}")
        print("Make sure Account A is allowed to call Account B in Telegram privacy settings.")

if __name__ == "__main__":
    asyncio.run(main())

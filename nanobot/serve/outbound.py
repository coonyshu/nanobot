"""
Outbound message consumer — background task that forwards bus messages
to the appropriate WebSocket connections.
"""

import asyncio

from loguru import logger

from .state import ServiceState


async def consume_outbound_messages(bus, svc: ServiceState):
    """Continuously consume ``bus.outbound`` and forward to WebSocket clients."""
    logger.info("Starting outbound message consumer loop")
    try:
        while True:
            msg = await bus.consume_outbound()
            logger.info("Outbound: channel={}, chat_id={}", msg.channel, msg.chat_id)

            if msg.channel == "voice":
                user_id = msg.chat_id
                _, session_data = svc.get_user_session(user_id)

                if session_data:
                    websocket = session_data["websocket"]
                    session_obj = session_data.get("session")

                    try:
                        payload = {"type": "text", "text": msg.content, "agent_name": msg.agent_name}
                        await websocket.send_json(payload)

                        if session_obj and hasattr(session_obj, "tts_text_queue"):
                            await session_obj.tts_text_queue.put(msg.content)
                    except Exception as e:
                        logger.error("Failed to send message to user {}: {}", user_id, e)
                else:
                    logger.warning("User {} not in active voice sessions, message dropped", user_id)
            else:
                logger.warning("Unsupported channel: {}, message dropped", msg.channel)

    except asyncio.CancelledError:
        logger.info("Outbound message consumer stopped")
        raise
    except Exception as e:
        logger.error("Outbound message consumer error: {}", e, exc_info=True)

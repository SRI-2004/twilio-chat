from fastapi import FastAPI, Depends, Request, HTTPException, Header
from sqlalchemy.orm import Session
from database import SessionLocal, engine, Base, get_db
from models import User, Bet
from pydantic import BaseModel
from twilio.rest import Client
from twilio.request_validator import RequestValidator
import httpx
import os
import json
import logging
from fastapi.responses import PlainTextResponse, Response
from datetime import datetime

# Initialize FastAPI app
app = FastAPI()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create the database tables
Base.metadata.create_all(bind=engine)

# Load environment variables
# Twilio Configuration
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')  # Now loaded from .env

# Validate that essential environment variables are present
if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER]):
    logger.error("Missing Twilio configuration in environment variables.")
    raise EnvironmentError("Missing Twilio configuration in environment variables.")

client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Bearer Token
BEARER_TOKEN = os.getenv('BEARER_TOKEN')

if not BEARER_TOKEN:
    logger.error("Missing BEARER_TOKEN in environment variables.")
    raise EnvironmentError("Missing BEARER_TOKEN in environment variables.")
# Initialize Twilio Client
# Twilio Signature Validator
validator = RequestValidator(TWILIO_AUTH_TOKEN)

# In-memory user session state
user_state = {}

# Global variable to store the sports list fetched from external API
sports_list = {}

# Function to fetch sports list from external API
async def fetch_sports():
    url = "https://gogrfgunpxkglyozdsyt.supabase.co/functions/v1/fetch_sports_list"
    headers = {
        "Authorization": f"Bearer {BEARER_TOKEN}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient() as client_async:
        try:
            response = await client_async.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            return data
        except httpx.HTTPError as e:
            logger.error(f"Error fetching sports list: {e}")
            return {}

# Function to fetch matches based on event_key
async def fetch_matches(event_key: str):
    url = f"https://gogrfgunpxkglyozdsyt.supabase.co/functions/v1/fetch_event_list?event_key={event_key}"
    headers = {
        "Authorization": f"Bearer {BEARER_TOKEN}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient() as client_async:
        try:
            response = await client_async.get(url, headers=headers)
            response.raise_for_status()
            matches = response.json()
            return matches
        except httpx.HTTPError as e:
            logger.error(f"Error fetching matches for event_key {event_key}: {e}")
            return []

# Function to send WhatsApp messages via Twilio
def send_whatsapp_message(to_number: str, body: str):
    client.messages.create(
        body=body,
        from_=TWILIO_PHONE_NUMBER,
        to=to_number
    )

# Startup event to fetch sports list and initialize the database with hardcoded bets
@app.on_event("startup")
async def startup_event():
    global sports_list
    sports_list = await fetch_sports()
    if not sports_list:
        logger.error("Failed to fetch sports list during startup.")
    else:
        logger.info("Sports list fetched successfully.")
    
    # Optional: Insert bets into the database based on fetched sports and tournaments
    db = SessionLocal()
    try:
        existing_bets = db.query(Bet).count()
        if existing_bets == 0:
            hardcoded_bets = []
            for sport, tournaments in sports_list.items():
                for tournament in tournaments:
                    if tournament.get("active"):
                        bet = Bet(
                            event_name=tournament.get("title"),
                            sport_key=tournament.get("key"),
                            cost=10  # Assign a default cost or fetch from another source
                        )
                        hardcoded_bets.append(bet)
            if hardcoded_bets:
                db.add_all(hardcoded_bets)
                db.commit()
                logger.info("Hardcoded bets inserted into the database.")
    except Exception as e:
        logger.error(f"Error inserting hardcoded bets: {e}")
    finally:
        db.close()

# Webhook endpoint to receive messages from Twilio
@app.post("/twilio-webhook/")
async def receive_message(request: Request, db: Session = Depends(get_db), x_twilio_signature: str = Header(None)):
    # Validate Twilio signature
    url = str(request.url)
    body = await request.body()
    
    # Parse the form data
    form = await request.form()
    
    # Ensure all parameter values are strings
    params = {key: str(value) for key, value in form.items()}
    
    if not validator.validate(url, params, x_twilio_signature):
        logger.warning("Invalid Twilio signature.")
        raise HTTPException(status_code=403, detail="Invalid signature")

    incoming_msg = form.get('Body', '').strip().lower()
    from_number = form.get('From', '').strip()

    if not from_number:
        logger.warning("No sender number found.")
        return Response(status_code=200)  # Return empty 200 response

    logger.info(f"Received message from {from_number}: {incoming_msg}")

    # Handle 'exit' command first
    if incoming_msg == "exit":
        if from_number in user_state:
            user_state.pop(from_number)
            exit_message = (
                "👋 You have exited the betting process.\n\n"
                "You can type:\n"
                "1️⃣ 'start' to begin selecting a sport and place a bet.\n"
                "2️⃣ 'my account' to check your current balance and referral code."
            )
            send_whatsapp_message(from_number, exit_message)
            logger.info(f"User {from_number} exited the betting process.")
            return Response(status_code=200)
    
    # Get user from database
    user = db.query(User).filter(User.whatsapp_number == from_number).first()

    # Handle unknown users
    if not user:
        # Generate a unique referral code (e.g., using UUID)
        import uuid
        referral_code = str(uuid.uuid4()).split('-')[0].upper()

        user = User(
            whatsapp_number=from_number,
            coins_balance=500,
            referral_code=referral_code
        )
        db.add(user)
        db.commit()
        welcome_message = (
            "🎉 Welcome to Opiniox, your ultimate opinion betting platform! 🎉\n\n"
            "Your account has been successfully created with 500 coins. Ready to place your bets and test your predictions?\n\n"
            "Here's what you can do:\n"
            "1️⃣ Type 'start' to begin selecting your sport and place a bet.\n"
            "2️⃣ Type 'my account' to check your current balance and referral code.\n\n"
            "Let the fun begin and may your bets be ever in your favor! 🚀"
        )
        send_whatsapp_message(from_number, welcome_message)
        logger.info(f"New user registered: {from_number}")
        # No return here; continue to process the incoming message

    # Retrieve user state from in-memory dictionary
    state = user_state.get(from_number, {"state": "idle"})

    current_state = state.get("state")

    # Handle different states
    if current_state == "select_sport":
        selected_sport_input = incoming_msg
        sports = list(sports_list.keys())
        sport_mapping = {str(idx): sport for idx, sport in enumerate(sports, start=1)}
        sport_mapping.update({sport.lower(): sport for sport in sports})

        selected_sport = sport_mapping.get(selected_sport_input)
        if selected_sport and selected_sport in sports_list:
            # Update user state to select_tournament with selected sport
            user_state[from_number] = {"state": "select_tournament", "sport": selected_sport}
            tournaments = sports_list[selected_sport]
            active_tournaments = [t for t in tournaments if t.get("active")]

            if not active_tournaments:
                send_whatsapp_message(from_number, "❌ No tournaments available for the selected sport.")
                user_state.pop(from_number, None)
                return Response(status_code=200)

            # List tournaments
            message = f"🏅 *{selected_sport} Tournaments:*\n"
            for idx, tournament in enumerate(active_tournaments, start=1):
                message += f"{idx}. {tournament.get('title')}\n"
            message += "\n➡️ Reply with the number of the tournament you'd like to bet on (e.g., '1').\n\n"
            message += "🔄 Type 'exit' anytime to leave the betting process."
            send_whatsapp_message(from_number, message)
            logger.info(f"User {from_number} selected sport: {selected_sport}")
        else:
            # Invalid sport selection
            message = "❌ *Invalid selection.* Please choose a valid sport by typing the corresponding number or sport name.\n\n"
            message += "🏆 *Available Sports:*\n"
            for idx, sport in enumerate(sports, start=1):
                message += f"{idx}. {sport}\n"
            message += "\n➡️ Reply with the sport name or number.\n\n"
            message += "🔄 Type 'exit' anytime to leave the betting process."
            send_whatsapp_message(from_number, message)
            logger.warning(f"User {from_number} made an invalid sport selection: {selected_sport_input}")

    elif current_state == "select_tournament":
        try:
            tournament_idx = int(incoming_msg) - 1
        except ValueError:
            send_whatsapp_message(from_number, "❌ *Invalid input.* Please reply with a number corresponding to the tournament.")
            logger.warning(f"User {from_number} provided non-integer tournament selection: {incoming_msg}")
            return Response(status_code=200)

        selected_sport = state.get("sport")
        tournaments = sports_list[selected_sport]
        active_tournaments = [t for t in tournaments if t.get("active")]

        if 0 <= tournament_idx < len(active_tournaments):
            selected_tournament = active_tournaments[tournament_idx]
            event_key = selected_tournament.get("key").lower().replace(" ", "_")  # Ensure correct format

            # Fetch matches from external API
            matches = await fetch_matches(event_key)
            if not matches:
                send_whatsapp_message(from_number, "❌ No matches found for the selected tournament.")
                user_state.pop(from_number, None)
                return Response(status_code=200)

            # Update user state to select_match with selected tournament and matches
            user_state[from_number] = {
                "state": "select_match",
                "sport": selected_sport,
                "tournament": selected_tournament.get("title"),
                "matches": matches  # Store matches for reference
            }

            # List matches
            message = f"⚽ *{selected_tournament.get('title')} Matches:*\n"
            for idx, match in enumerate(matches, start=1):
                commence_time = match.get("commence_time")
                home_team = match.get("home_team")
                away_team = match.get("away_team")
                try:
                    # Parse and format the commence_time
                    dt = datetime.strptime(commence_time, "%Y-%m-%dT%H:%M:%SZ")
                    formatted_time = dt.strftime("%B %d, %Y at %H:%M UTC")
                except ValueError:
                    # If parsing fails, use the original string
                    formatted_time = commence_time
                message += f"{idx}. {home_team} vs {away_team} at {formatted_time}\n"
            message += "\n➡️ Reply with the number of the match you'd like to bet on (e.g., '1').\n\n"
            message += "🔄 Type 'exit' anytime to leave the betting process."
            send_whatsapp_message(from_number, message)
            logger.info(f"User {from_number} selected tournament: {selected_tournament.get('title')}, fetched matches.")
        else:
            # Invalid tournament selection
            send_whatsapp_message(from_number, "❌ *Invalid selection.* Please choose a valid tournament by typing the corresponding number.")
            logger.warning(f"User {from_number} made an invalid tournament selection: {incoming_msg}")

    elif current_state == "select_match":
        try:
            match_idx = int(incoming_msg) - 1
        except ValueError:
            send_whatsapp_message(from_number, "❌ *Invalid input.* Please reply with a number corresponding to the match.")
            logger.warning(f"User {from_number} provided non-integer match selection: {incoming_msg}")
            return Response(status_code=200)

        matches = state.get("matches", [])
        if 0 <= match_idx < len(matches):
            selected_match = matches[match_idx]
            match_id = selected_match.get("id")
            home_team = selected_match.get("home_team")
            away_team = selected_match.get("away_team")
            commence_time = selected_match.get("commence_time")
            odds = selected_match.get("odds", {})
            outcomes = odds.get("outcomes", [])

            if not outcomes:
                send_whatsapp_message(from_number, "❌ *No betting outcomes available for this match.* Please select another match.")
                logger.warning(f"Match {match_id} has no betting outcomes.")
                return Response(status_code=200)

            try:
                # Parse and format the commence_time
                dt = datetime.strptime(commence_time, "%Y-%m-%dT%H:%M:%SZ")
                formatted_time = dt.strftime("%B %d, %Y at %H:%M UTC")
            except ValueError:
                # If parsing fails, use the original string
                formatted_time = commence_time

            # Display match details and outcomes
            message = (
                f"🏟️ *Match Selected:*\n"
                f"{home_team} vs {away_team}\n"
                f"Commence Time: {formatted_time}\n\n"
                f"*Available Outcomes:*\n"
            )
            for idx, outcome in enumerate(outcomes, start=1):
                team = outcome.get("name")
                price = outcome.get("price")
                message += f"{idx}. {team}: {price}\n"
            message += "\n➡️ To place a bet, type 'bet {number}' corresponding to your chosen outcome (e.g., 'bet 1').\n\n"
            message += "🔄 Type 'exit' anytime to leave the betting process."
            send_whatsapp_message(from_number, message)
            logger.info(f"User {from_number} selected match: {home_team} vs {away_team}, awaiting bet.")
            
            # Update state to place_bet with selected match details
            user_state[from_number] = {
                "state": "place_bet",
                "sport": state.get("sport"),
                "tournament": state.get("tournament"),
                "match": selected_match
            }
        else:
            # Invalid match selection
            send_whatsapp_message(from_number, "❌ *Invalid selection.* Please choose a valid match by typing the corresponding number.")
            logger.warning(f"User {from_number} made an invalid match selection: {incoming_msg}")

    elif current_state == "place_bet":
        # Expected input: 'bet {number}', e.g., 'bet 1'
        if incoming_msg.startswith("bet"):
            parts = incoming_msg.split()
            if len(parts) != 2 or not parts[1].isdigit():
                send_whatsapp_message(from_number, "❌ *Invalid command format.* Please type 'bet {number}' (e.g., 'bet 1').")
                logger.warning(f"User {from_number} provided invalid bet command: {incoming_msg}")
                return Response(status_code=200)

            bet_choice = int(parts[1]) - 1
            match = state.get("match")
            if not match:
                send_whatsapp_message(from_number, "❌ *No match selected.* Please start the betting process again by typing 'start'.")
                user_state.pop(from_number, None)
                logger.error(f"User {from_number} has no match in state during place_bet.")
                return Response(status_code=200)

            odds = match.get("odds", {})
            outcomes = odds.get("outcomes", [])
            if 0 <= bet_choice < len(outcomes):
                selected_outcome = outcomes[bet_choice]
                selected_team = selected_outcome.get("name")
                price = selected_outcome.get("price")
                
                # Define bet cost
                bet_cost = 10  # You can make this dynamic or allow user to specify
                
                if user.coins_balance >= bet_cost:
                    # Deduct coins and create bet record
                    user.coins_balance -= bet_cost
                    new_bet = Bet(
                        event_name=f"{match.get('home_team')} vs {match.get('away_team')}",
                        sport_key=match.get("sport_key"),
                        match_id=match.get("id"),
                        cost=bet_cost,
                        user_id=user.user_id,
                        status="placed"
                    )
                    db.add(new_bet)
                    db.commit()

                    confirmation_message = (
                        f"✅ *Bet Placed!*\n"
                        f"Team: {selected_team}\n"
                        f"Odds: {price}\n"
                        f"Cost: {bet_cost} coins\n"
                        f"Remaining Balance: {user.coins_balance} coins.\n\n"
                        "Type 'start' to place another bet or 'my account' to view your details.\n\n"
                        "🔄 Type 'exit' anytime to leave the betting process."
                    )
                    send_whatsapp_message(from_number, confirmation_message)
                    logger.info(f"User {from_number} placed a bet on {selected_team} with price {price}")
                    
                    # Reset user state
                    user_state.pop(from_number, None)
                else:
                    send_whatsapp_message(from_number, "❌ *Insufficient balance* to place the bet.")
                    logger.warning(f"User {from_number} has insufficient balance for the bet.")
            else:
                send_whatsapp_message(from_number, "❌ *Invalid bet selection.* Please choose a valid outcome by typing the corresponding number.")
                logger.warning(f"User {from_number} made an invalid bet selection: {parts[1]}")
        else:
            send_whatsapp_message(from_number, "❌ *Invalid command.* Please type 'bet {number}' to place a bet (e.g., 'bet 1').")
            logger.warning(f"User {from_number} sent an invalid command during place_bet: {incoming_msg}")

    else:
        # Handle commands
        if incoming_msg == "start":
            user_state[from_number] = {"state": "select_sport"}
            # List sports
            sports = list(sports_list.keys())
            message = "🏆 *Select a Sport:*\n"
            for idx, sport in enumerate(sports, start=1):
                message += f"{idx}. {sport}\n"
            message += "\n➡️ Reply with the sport name or number.\n\n"
            message += "🔄 Type 'exit' anytime to leave the betting process."
            send_whatsapp_message(from_number, message)
            logger.info(f"User {from_number} initiated 'start' command.")
        elif incoming_msg == "my account":
            # Fetch the user's bet history, ordered by the most recent bets first
            bets = db.query(Bet).filter(Bet.user_id == user.user_id).order_by(Bet.bet_id.desc()).limit(5).all()
    
             # Start composing the account message
            account_message = (
                f"💰 *Your Account Details:*\n"
                f"🔹 Balance: {user.coins_balance} coins\n"
                f"🔹 Referral Code: {user.referral_code}\n\n"
                f"📜 *Your Recent Bet History:*\n"
            )
    
            if bets:
                for idx, bet in enumerate(bets, start=1):
                    # Format the status with appropriate emojis
                    if bet.status.lower() == "placed":
                        status_emoji = "🔵"
                    elif bet.status.lower() == "won":
                        status_emoji = "🟢"
                    elif bet.status.lower() == "lost":
                        status_emoji = "🔴"
                    else:
                        status_emoji = "⚪"
            
                    account_message += (
                        f"{idx}. *Event:* {bet.event_name}\n"
                        f"   *Match ID:* {bet.match_id}\n"
                        f"   *Cost:* {bet.cost} coins\n"
                        f"   *Status:* {status_emoji} {bet.status.capitalize()}\n\n"
                    )
        
                    # Check if there are more bets beyond the displayed limit
                total_bets = db.query(Bet).filter(Bet.user_id == user.user_id).count()
                if total_bets > 5:
                    account_message += f"...and {total_bets - 5} more bets. Visit 'my account' for the complete history."
            else:
                account_message += "You haven't placed any bets yet.\n\n"
    
                    # Add navigation options
                account_message += (
                    "🔄 Type 'start' to place a new bet or 'exit' to return to the main menu."
                )
    
            # Send the composed message to the user
            send_whatsapp_message(from_number, account_message)
            logger.info(f"User {from_number} requested account details and bet history.")

        else:
            # Invalid command
            message = (
                "❓ *Invalid command.*\n\n"
                "You can:\n"
                "1. Type 'start' to select a sport and place a bet.\n"
                "2. Type 'my account' to check your balance.\n\n"
                "🔄 Type 'exit' anytime to leave the betting process."
            )
            send_whatsapp_message(from_number, message)
            logger.warning(f"User {from_number} sent an invalid command: {incoming_msg}")

        return Response(status_code=200)  # Return empty 200 response to Twilio
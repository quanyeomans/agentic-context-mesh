# Run the subscription in a background thread
import threading
threading.Thread(target=subscribe_to_reviews, daemon=True).start()
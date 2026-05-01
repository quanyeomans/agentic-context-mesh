# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ChatService")

app = FastAPI()
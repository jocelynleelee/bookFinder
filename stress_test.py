from locust import HttpUser, task, between

class BookFinderUser(HttpUser):
    wait_time = between(1, 3)

    @task(3)
    def index(self):
        self.client.get("/")

    @task(2)
    def books(self):
        self.client.get("/books?page=1")

    @task(1)
    def search(self):
        self.client.get("/search?q=animals&source=library")

    @task(1)
    def bookoutlet(self):
        self.client.get("/bookoutlet")
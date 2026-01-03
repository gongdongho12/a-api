.PHONY: install db-init run clean

install:
	pip install -r requirements.txt

db-init:
	python3 -m app.db.init_db

run:
	python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

db-start:
	docker run -d \
	  --name local-db \
	  -p 3306:3306 \
	  -e MYSQL_ROOT_PASSWORD=root \
	  -e MYSQL_DATABASE=db \
	  -e MYSQL_USER=dongho1596 \
	  -e MYSQL_PASSWORD=dongho135 \
	  mysql:8.0

db-stop:
	docker stop local-db && docker rm local-db

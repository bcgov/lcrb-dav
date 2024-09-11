FROM python:3.12-bullseye AS main

WORKDIR /app

COPY dav-controller/requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

COPY dav-controller ./

EXPOSE 5000

RUN ["chmod", "+x", "./docker-entrypoint.sh"]

ENTRYPOINT ["./docker-entrypoint.sh"]
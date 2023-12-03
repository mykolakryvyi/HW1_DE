from airflow import DAG
from airflow.providers.http.sensors.http import HttpSensor
from airflow.providers.http.operators.http import SimpleHttpOperator
from airflow.providers.postgres.operators.postgres import PostgresOperator
from airflow.operators.python_operator import PythonOperator
from airflow.decorators import task_group
from airflow.utils.dates import days_ago
from airflow.models import Variable
import json
from datetime import datetime
import logging

def _get_weather(ti, city):
    info = ti.xcom_pull(f"extract_group_{city}.extract_data_{city}")
    timestamp = info["data"][0]["dt"]
    timestamp = datetime.utcfromtimestamp(int(timestamp)).strftime('%Y-%m-%d')
    t = info["data"][0]["temp"]
    h = info["data"][0]["humidity"]
    c = info["data"][0]["clouds"]
    ws = info["data"][0]["wind_speed"]

    return timestamp, t, h, c, ws

def _api_call(execution_date, city):
    logging.info(
        f"{city} city, {execution_date} exec_Date_form, {int(datetime.fromisoformat(execution_date).timestamp())} dt"
    )
    execution_date = datetime.fromisoformat(execution_date)
    t = int(execution_date.timestamp())
    logging.info(f"{city} city, {execution_date} date, {t} t")

    lat, lon = coordinates[city]
    return lat, lon, t


def create_groups(city):
    @task_group(group_id=f"extract_group_{city}")
    def city_group():
        api_call = PythonOperator(
            task_id=f"api_call_{city}",
            python_callable=_api_call,
            op_kwargs={"city": city, "execution_date": "{{ ds }}"},
        )

        extract_data = SimpleHttpOperator(
            task_id=f"extract_data_{city}",
            http_conn_id="weather_conn",
            endpoint="data/3.0/onecall/timemachine",
            data={
                "lat": "{{ ti.xcom_pull(task_ids='extract_group_" + city + ".api_call_" + city +  "')[0] }}",
                "lon": "{{ ti.xcom_pull(task_ids='extract_group_" + city + ".api_call_" + city + "')[1] }}",
                "dt": "{{ ti.xcom_pull(task_ids='extract_group_" + city + ".api_call_" + city + "')[2] }}",
                "appid": 'f8683b5042f60d16d2dd1d8996ae9f2a',
            },
            method="GET",
            response_filter=lambda response: json.loads(response.text),
            log_response=True,
        )

        api_call >> extract_data

    return city_group




def create_group(city):
    @task_group(group_id=f"transform_{city}")
    def transform_group():
        transform_data = PythonOperator(
            task_id=f"process_{city}",
            python_callable=_get_weather,
            op_kwargs={"city": city},
        )

        push = PostgresOperator(
            task_id=f"push_{city}",
            postgres_conn_id="hw1_postgres",
            sql="""
                INSERT INTO weather (city, time, t, h, c, ws) 
                VALUES (%s, %s, %s, %s, %s, %s);
                """,
            parameters=[
                city,
                "{{ ti.xcom_pull(task_ids='transform_" + city + ".process_" + city + "')[0] }}",
                "{{ ti.xcom_pull(task_ids='transform_" + city + ".process_" + city + "')[1] }}",
                "{{ ti.xcom_pull(task_ids='transform_" + city + ".process_" + city + "')[2] }}",
                "{{ ti.xcom_pull(task_ids='transform_" + city + ".process_" + city + "')[3] }}",
                "{{ ti.xcom_pull(task_ids='transform_" + city + ".process_" + city + "')[4] }}",
            ],
        )

        transform_data >> push

    return transform_group


with DAG("weather_scrapper", start_date=datetime(2023, 11, 23), schedule_interval="@daily", catchup=True) as dag:
    coordinates = {
        "Lviv": ("49.8397", "24.0297"),
        "Kyiv": ("50.4501", "30.5234"),
        "Kharkiv": ("49.9935", "36.2304"),
        "Odesa": ("46.4825", "30.7233"),
        "Zhmerynka": ("49.0384", "28.1056"),
    }

    create_table_weather = PostgresOperator(
        task_id="create_table",
        postgres_conn_id="hw1_postgres",
        sql="""
        CREATE TABLE IF NOT EXISTS weather (
            city VARCHAR(255),
            time TIMESTAMP,
            t FLOAT,
            h FLOAT,
            c FLOAT,
            ws FLOAT
        );
        """,
    )


    extract_groups = [create_groups(city)() for city in coordinates]
    for group in extract_groups:
        create_table_weather >> group

    transform_push_groups = [
        create_group(city)() for city in coordinates
    ]
    for extract_group, transform_push_group in zip(
        extract_groups, transform_push_groups
    ):
        extract_group >> transform_push_group

from fastapi import FastAPI, HTTPException, Query, Depends
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session
from models import DomainLookup, SessionLocal, Base
import aioredis
import json
import requests
import os
import re

app = FastAPI()
BASE_URL = "https://www.ps.kz/domains/whois/result"

REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379')
redis = aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)

@app.on_event("startup")
async def startup_event():
    global redis
    redis = aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)

@app.on_event("shutdown")
async def shutdown_event():
    await redis.close()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def parse_domain_info(data):
    info = {}
    info['Domain Name'] = re.search(r'Доменное имя:\n(.+)', data).group(1).strip()
    info['Status'] = re.search(r'Статус:\n(.+)', data).group(1).strip()
    info['Registrar'] = re.search(r'Регистратор:\n(.+)', data).group(1).strip()

    name_servers_match = re.search(r'Серверы имен:\n(.*?)\nСоздан:', data, re.DOTALL)
    if name_servers_match:
        info['Name Servers'] = [ns.strip() for ns in name_servers_match.group(1).strip().split('\n') if ns.strip()]

    created_on_match = re.search(r'Создан:\n(.+)', data)
    if created_on_match:
        info['Created On'] = created_on_match.group(1).strip().split()[0]

    last_updated_on_match = re.search(r'Последнее изменение:\n(.+)', data)
    if last_updated_on_match:
        info['Last Updated On'] = last_updated_on_match.group(1).strip().split()[0]

    expiration_date_match = re.search(r'Дата окончания:\n(.+)', data)
    if expiration_date_match:
        info['Expiration Date'] = expiration_date_match.group(1).strip().split()[0]

    return info




@app.get('/lookup_whois/')
async def lookup_whois(domain_name: str = Query(..., description="The domain name to look up"), db: Session = Depends(get_db)):
    cached_result = await redis.get(domain_name)
    if cached_result:
        return json.loads(cached_result)

    if '.' not in domain_name:
        raise HTTPException(status_code=404, detail=f"Incorrect domain name")

    params = {'q': domain_name}
    response = requests.post(BASE_URL, params=params)

    is_occupied = response.text.find("После окончания этого периода он будет удалён на")
    is_loaded_page = response.text.find("Возникли непредвиденные проблемы. Попробуйте еще раз через несколько минут.")

    if is_loaded_page >= 0:
        raise HTTPException(status_code=404, detail=f"Возникли непредвиденные проблемы. Попробуйте еще раз через несколько минут.")

    if response.status_code == 200 and is_occupied != -1:
        soup = BeautifulSoup(response.text, 'html.parser')
        data = soup.find_all('td')
        info_text = str()
        for each in data:
            line = each.text.strip()
            info_text += line + '\n'
        if ':' not in info_text:
            parsed_info = {}
        else: 
            parsed_info = parse_domain_info(info_text)

        result = {}
        result['domain_name'] = parsed_info.get('Domain Name', domain_name)
        result['status'] = parsed_info.get('Status', 'occupied')
        result['registrar'] = parsed_info.get('Registrar', 'N/A')
        result['name_servers'] = parsed_info.get('Name Servers', 'N/A')
        result['created_on'] = parsed_info.get('Created On', 'N/A')
        result['last_updated_on'] = parsed_info.get('Last Updated On', 'N/A')
        result['expiration_date'] = parsed_info.get('Expiration Date', 'N/A')


        lookup = DomainLookup(domain_name=domain_name, status=result['status'], registrar = result['registrar'], name_servers = result['name_servers'], created_on = result['created_on'], last_updated_on = result['last_updated_on'], expiration_date = result['expiration_date'])
        db.add(lookup)
        db.commit()
        await redis.setex(domain_name, 3600, json.dumps(result))

        return result
    else:
        raise HTTPException(status_code=200, detail=f"Domain Name is not occupied")

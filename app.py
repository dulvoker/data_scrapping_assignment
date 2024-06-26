from fastapi import FastAPI, HTTPException, Query, Depends
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session
from models import DomainLookup, SessionLocal
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
        if last_updated_on_match == 'не': last_updated_on_match = 'не производоилось.'
        info['Last Updated On'] = last_updated_on_match.group(1).strip().split()[0]

    expiration_date_match = re.search(r'Дата окончания:\n(.+)', data)
    if expiration_date_match:
        info['Expiration Date'] = expiration_date_match.group(1).strip().split()[0]

    return info

def domain_exists(db: Session, domain_name: str) -> bool:
    return db.query(DomainLookup).filter(DomainLookup.domain_name == domain_name).first()


@app.get('/lookup_whois/')
async def lookup_whois(domain_name: str = Query(..., description="The domain name to look up"), db: Session = Depends(get_db)):
    cached_result = await redis.get(domain_name)
    if cached_result:
        return json.loads(cached_result)
    
    existing_record = domain_exists(db, domain_name)
    if existing_record:
        return {
            'domain_name': existing_record.domain_name,
            'status': existing_record.status,
            'registrar': existing_record.registrar,
            'name_servers': existing_record.name_servers,
            'created_on': existing_record.created_on,
            'last_updated_on': existing_record.last_updated_on,
            'expiration_date': existing_record.expiration_date,
            'timestamp': existing_record.timestamp
        }
    
    if '.' not in domain_name:
        raise HTTPException(status_code=400, detail="Incorrect domain name")

    params = {'q': domain_name}
    response = requests.post(BASE_URL, params=params)

    if "Возникли непредвиденные проблемы. Попробуйте еще раз через несколько минут." in response.text:
        raise HTTPException(status_code=503, detail="Возникли непредвиденные проблемы. Попробуйте еще раз через несколько минут.")

    is_not_occupied = "доступен для регистрации." in response.text

    if response.status_code == 200 and not is_not_occupied:
        soup = BeautifulSoup(response.text, 'html.parser')
        data = soup.find_all('td')
        info_text = '\n'.join(each.text.strip() for each in data)
        parsed_info = parse_domain_info(info_text) if ':' in info_text else {}

        result = {
            'domain_name': parsed_info.get('Domain Name', domain_name),
            'status': parsed_info.get('Status', 'occupied'),
            'registrar': parsed_info.get('Registrar', 'N/A'),
            'name_servers': parsed_info.get('Name Servers', 'N/A'),
            'created_on': parsed_info.get('Created On', 'N/A'),
            'last_updated_on': parsed_info.get('Last Updated On', 'N/A'),
            'expiration_date': parsed_info.get('Expiration Date', 'N/A')
        }

        lookup = DomainLookup(
            domain_name=domain_name, 
            status=result['status'], 
            registrar=result['registrar'], 
            name_servers=result['name_servers'], 
            created_on=result['created_on'], 
            last_updated_on=result['last_updated_on'], 
            expiration_date=result['expiration_date']
        )
        db.add(lookup)
        db.commit()
        await redis.setex(domain_name, 3600, json.dumps(result))

        return result
    else:
        raise HTTPException(status_code=404, detail="Domain Name is not occupied")
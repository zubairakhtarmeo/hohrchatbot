#!/usr/bin/env python3
import requests
import json

try:
    r = requests.get('http://127.0.0.1:5000/api/status', timeout=5)
    d = r.json()
    
    print('\n✅ CHATBOT ONLINE & READY\n')
    print('='*70)
    print('LOCAL ACCESS (This Computer)')
    print('='*70)
    print('📊 Employee Chat:  http://localhost:5000')
    print('🔐 HR Master:      http://localhost:5000/master')
    
    print('\n' + '='*70)
    print('ORG/INTRANET ACCESS (Other Laptops on Network)')
    print('='*70)
    org_url = d.get('org_url') or d.get('lan_url') or 'http://192.168.100.92:5000'
    print(f'📊 Employee Chat:  {org_url}')
    print(f'🔐 HR Master:      {org_url}/master')
    
    print('\n' + '='*70)
    print('STATUS')
    print('='*70)
    print(f'Server Status:  {d["status"]}')
    print(f'Documents:      {d["documents"]} policies indexed')
    print(f'Current Mode:   {d.get("mode", "Smart")}')
    print(f'Reasoning:      {d.get("model", "Ollama / llama3.2:1b")}')
    print()
    
except Exception as e:
    print(f'❌ Error: {e}')

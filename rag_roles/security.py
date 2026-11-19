"""Shared server-side policy. Unknown roles/ACLs fail closed."""
import json

ROLES = {
 'RAG_USER': 'Обычный пользователь',
 'RAG_ACCOUNTANT': 'Бухгалтер',
 'RAG_REPORTING_SPECIALIST': 'Специалист по отчётности',
 'RAG_ADMIN': 'Администратор',
 'RAG_SECURITY_AUDITOR': 'Специалист по ИБ',
}
LEVELS = {'general':'Общий', 'internal':'Внутренний', 'restricted':'Ограниченный'}
CLEARANCE = dict.fromkeys(ROLES, 2)
CLEARANCE['RAG_USER'] = 0
NAV = [('search','Поиск'),('history','История'),('documents','Документы'),('upload','Загрузка'),('index','Индексация'),('users','Пользователи'),('settings','Настройки'),('audit','Журнал аудита'),('security','События безопасности'),('system','Система'),('about','О системе')]
PERMISSIONS = {role: {'search', 'history', 'about'} for role in ROLES}
PERMISSIONS['RAG_ACCOUNTANT'] |= {'documents'}
PERMISSIONS['RAG_REPORTING_SPECIALIST'] |= {'documents'}
PERMISSIONS['RAG_ADMIN'] |= {key for key, _ in NAV}
# Preserve the prototype's read-only search/document access for auditors.
PERMISSIONS['RAG_SECURITY_AUDITOR'] |= {'documents', 'audit', 'security'}


def allowed(user, document):
    if not user or not user['active'] or user['role'] not in ROLES:
        return False
    if user['role'] == 'RAG_ADMIN':
        return True
    try:
        acl = json.loads(document['roles'])
        return (isinstance(acl, list) and user['role'] in acl
                and list(LEVELS).index(document['level']) <= CLEARANCE[user['role']])
    except (ValueError, TypeError):
        return False

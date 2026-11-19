from pathlib import Path
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

out = Path(__file__).parent / 'Мини отчёт о входе администратора.docx'
doc = Document()
sec = doc.sections[0]
sec.page_width, sec.page_height = Inches(8.5), Inches(11)
sec.top_margin = sec.bottom_margin = Inches(.65)
sec.left_margin = sec.right_margin = Inches(.75)
for name in ['Normal', 'Title', 'Subtitle', 'Heading 1', 'Heading 2']:
    style = doc.styles[name]
    style.font.name = 'Calibri'
    style.font.color.rgb = RGBColor(0, 0, 0)
    style.paragraph_format.space_after = Pt(5)
normal = doc.styles['Normal']
normal.font.size = Pt(11)
normal.paragraph_format.line_spacing = 1.05
doc.styles['Title'].font.size = Pt(20)
doc.styles['Title'].paragraph_format.space_after = Pt(7)
heading = doc.styles['Heading 1']
heading.font.size = Pt(12)
heading.font.bold = True
heading.paragraph_format.space_before = Pt(10)
heading.paragraph_format.space_after = Pt(4)

doc.add_paragraph('Двухэтапная аутентификация администратора', 'Title')
p = doc.add_paragraph('Мини отчёт по проекту Corporate RAG   •   12 октября 2026 года')
p.runs[0].font.size = Pt(10)
doc.add_paragraph('В проекте реализован вход администратора по паролю и одноразовому коду, создаваемому на основном компьютере. Механизм ограничивает административный доступ при раскрытии только пароля сайта. До подтверждения кода защищённые страницы и API недоступны.')

doc.add_paragraph('Порядок входа', 'Heading 1')
doc.add_paragraph('Администратор вводит пароль на /admin/login, затем код на /admin/verify. Ожидание второго этапа ограничено 10 минутами. Полноценная сессия создаётся только после кода. Обычные пользователи входят через /login; административный вход через эту форму запрещён.')
doc.add_paragraph('Код создаётся в PowerShell из каталога C:\\corporate-rag командой:')
p = doc.add_paragraph()
r = p.add_run(r'.\.venv\Scripts\python.exe -B .\rag_roles\admin_access.py generate --login admin')
r.font.name = 'Consolas'; r.font.size = Pt(9)
p.paragraph_format.space_after = Pt(6)
doc.add_paragraph('Утилита использует каталог RAG_ROLES_DATA сервера и показывает код со сроком действия в терминале. Веб-генерации нет. В --login укажите логин администратора.')

doc.add_paragraph('Параметры и защита', 'Heading 1')
doc.add_paragraph('Код содержит 16 случайных символов с энтропией 80 бит. В базе хранится только SHA-256 хеш. Срок действия — 600 секунд с генерации; на границе срока код уже недействителен. Код применяется один раз, а новый отменяет предыдущие неиспользованные коды этого администратора.')
doc.add_paragraph('Пять ошибок закрывают текущую попытку. Дополнительно действует лимит 10 проверок за скользящие 600 секунд на учётную запись. Очистка cookie, смена IP и повторный первый этап его не сбрасывают. Погашение кода и создание сессии выполняются атомарно в SQLite, поэтому конкурентные запросы не дают два успешных входа.')
doc.add_paragraph('Сервер повторно проверяет роль, активность, версию учётной записи и сроки. Смена роли или пароля, отключение и удаление пользователя отзывают сессии, попытки и неиспользованные коды. Временный пароль также требует второго этапа. Применены CSRF и запрет кеширования; аудит не содержит секретов. Администратор выходит после 5 минут бездействия; срок кода не ограничивает активную сессию.')

doc.add_paragraph('Реализация и проверка', 'Heading 1')
doc.add_paragraph('Основные модули — rag_roles/server.py, admin_auth.py, admin_access.py, storage.py и sessions.py. Перед первой миграцией заполненной базы создаётся резервная копия и однократно отзываются прежние административные сессии. Пользователи, документы, права и история сохраняются.')
doc.add_paragraph('По отчёту от 9 октября 2026 года пройдены 103 теста, включая 25 новых: сроки, повторное использование, конкурентные запросы, лимиты, отзыв доступа и сохранность данных. Формы проверены в браузере на временной базе. Реальный вход через Tailscale в этом этапе не проверялся.')

doc.add_paragraph('Ограничение второго фактора', 'Heading 1')
doc.add_paragraph('Локальная команда не подтверждает физическое присутствие: её можно выполнить через удалённый доступ к Windows. Независимость второго фактора требует отдельно защищённого доступа к основному ПК. Обычным пользователям сайта нельзя предоставлять доступ к базе, ключу сессии, резервным копиям и файлам проекта. Права Windows и конфигурация Tailscale автоматически не изменяются.')
p = doc.add_paragraph('Основание: README.md, rag_roles/VERIFICATION.md и код механизма входа проекта.')
p.runs[0].font.size = Pt(9)
doc.core_properties.title = 'Двухэтапная аутентификация администратора'
doc.core_properties.subject = 'Мини отчёт по Corporate RAG'
doc.core_properties.author = ''
doc.core_properties.keywords = 'аутентификация, администратор, одноразовый код, Corporate RAG'
doc.save(out)
print(out)

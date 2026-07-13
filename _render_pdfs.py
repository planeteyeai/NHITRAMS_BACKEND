import fitz
from pathlib import Path

base = Path(r'C:\Users\planeteye01\Desktop\nhit')
for name in ['sample_distress.pdf', 'sample_inventory.pdf']:
    doc = fitz.open(base / name)
    stem = name.replace('.pdf', '')
    for i, page in enumerate(doc):
        pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
        out = base / f'{stem}_p{i + 1}.png'
        pix.save(str(out))
        print('saved', out.name, pix.width, 'x', pix.height)
    doc.close()

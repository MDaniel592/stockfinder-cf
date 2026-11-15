"""Join curated images by stable product ID, retaining asset provenance."""
import json
from pathlib import Path
import re

def enrich_images(products, root):
    root = Path(root)
    known = {product['id'] for product in products}
    images = {}
    for manifest in sorted((root/'data/images').glob('*.json')):
        for image in json.loads(manifest.read_text()):
            identity = image['product_id']
            if identity not in known: raise ValueError(f'Image refers to unknown product: {identity}')
            retailer = image.get('image_origin') == 'retailer'   # shop fallback image: manufacturer/curated always wins
            if identity in images:
                if images[identity][0] == retailer: raise ValueError(f'Duplicate image identity: {identity}')
                if not images[identity][0]: continue
            path = image['image_path']
            if not re.fullmatch(r'/images/products/[a-zA-Z0-9_/-]+\.(png|jpe?g|webp|svg)',path):
                raise ValueError('Invalid local image path')
            if image['image_scope'] not in ('model','family') or not image['image_source_url'].startswith('https://'):
                raise ValueError('Image provenance required')
            images[identity] = (retailer,{key:image[key] for key in ['image_path','image_source_url','image_scope']})
    return [{**product,**images.get(product['id'],(0,{}))[1]} for product in products]

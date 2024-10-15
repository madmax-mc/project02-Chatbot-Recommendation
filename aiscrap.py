import time
from flask import Flask, jsonify
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import chromedriver_autoinstaller
from neo4j import GraphDatabase

# Setup Chrome options
chrome_options = webdriver.ChromeOptions()
chrome_options.add_argument('--headless')
chrome_options.add_argument('--no-sandbox')
chrome_options.add_argument('--disable-dev-shm-usage')
chrome_options.add_argument(
    'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36')

# Automatically install chromedriver
chromedriver_autoinstaller.install()

# Neo4j credentials
URI = "neo4j://localhost"
AUTH = ("neo4j", "password")

# Initialize Flask app
app = Flask(__name__)

# Neo4j connection
def run_query(query, parameters=None):
    with GraphDatabase.driver(URI, auth=AUTH) as driver:
        with driver.session() as session:
            result = session.run(query, parameters)
            return [record for record in result]

# Function to scrape product links from the main listing page
def scrape_product_links(url):
    driver = webdriver.Chrome(options=chrome_options)
    driver.get(url)

    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CLASS_NAME, 'products-grid'))
        )
    except Exception as e:
        driver.quit()
        print(f"Error loading page: {e}")
        return []

    page_source = driver.page_source
    soup = BeautifulSoup(page_source, 'html.parser')
    product_cards = soup.find_all('div', class_='product-item')

    product_links = []
    for product in product_cards:
        product_link = product.find(
            'a', class_='product-card-image-container')['href']
        full_link = f"https://www.sephora.co.th{product_link}"
        product_links.append(full_link)

    driver.quit()
    return product_links

# Function to scrape product details from each individual product page
def scrape_product_details(product_url):
    driver = webdriver.Chrome(options=chrome_options)
    driver.get(product_url)

    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CLASS_NAME, 'product-detail-title'))
        )
    except Exception as e:
        driver.quit()
        print(f"Error loading product page: {e}")
        return {}

    page_source = driver.page_source
    soup = BeautifulSoup(page_source, 'html.parser')

    # Initialize an empty dictionary for product details
    product_details = {}

    # ดึงลิงก์รูปภาพ
    image_element = soup.find("img", class_="product-card-image")
    product_details["image_url"] = image_element["src"] if image_element else None

    # Extract "ฟังก์ชัน", "สูตร", "คุณประโยชน์" from product filter type
    filter_types = {
        "function": "ฟังก์ชัน: ",
        "formula": "สูตร: ",
        "benefits": "คุณประโยชน์: "
    }
    
    for key, label in filter_types.items():
        element = soup.find("span", class_="product-filter-type", string=label)
        product_details[key] = element.find_next("span", class_="product-filter-values").text.strip() if element else None

    # Extract product description
    product_description_header = soup.find("div", class_="description-attribute-header", string="ข้อมูลผลิตภัณฑ์")
    product_details["product_description"] = product_description_header.find_next("div").text.strip() if product_description_header else None

    # Extract benefits list or paragraph
    benefits_header = soup.find("div", class_="description-attribute-header", string="คุณประโยชน์")

    if benefits_header:
        # Try to find the next div after the header containing benefits
        benefits_div = benefits_header.find_next("div")

        if benefits_div:
            # Initialize the benefits list
            product_details["benefits_list"] = []

            # Try to find <p> inside the <div> for text-based benefits
            benefits_paragraph = benefits_div.find("p")
            if benefits_paragraph:
                product_details["benefits_list"].append(benefits_paragraph.text.strip())

            # Try to find <ul> for list-based benefits and append all <li> items to the list
            for ul in benefits_div.find_all("ul"):
                for li in ul.find_all("li"):
                    # Ensure proper encoding for each benefit
                    benefit_text = li.text.strip().encode('utf-8').decode('utf-8')
                    product_details["benefits_list"].append(benefit_text)

            # If no benefits were found, set to None
            if not product_details["benefits_list"]:
                product_details["benefits_list"] = None
        else:
            product_details["benefits_list"] = None
    else:
        product_details["benefits_list"] = None

    # Extract ingredients information from multiple locations
    product_ingredients = soup.find("div", class_="product-ingredients")

    # Initialize an empty dictionary for ingredient-related data
    product_details["ingredients_claims"] = None
    product_details["ingredients_list"] = None

    if product_ingredients:
        # Look for the 'variant-ingredients-values' under 'Product Claims'
        claims_span = product_ingredients.find("span", class_="variant-ingredients-values")
        if claims_span:
            product_details["ingredients_claims"] = claims_span.text.strip()

        # Look for the full ingredient list under 'product-ingredients-values'
        ingredients_list_div = product_ingredients.find("div", class_="product-ingredients-values")
        if ingredients_list_div:
            product_details["ingredients_list"] = ingredients_list_div.text.strip()

    # Extract how to use instructions
    how_to_use_header = soup.find("h3", class_="product-detail-title", string="วิธีการใช้งาน")
    product_details["how_to_use"] = how_to_use_header.find_next("div", class_="product-how-to-text").text.strip() if how_to_use_header else None

    # Extract set contents or features from "ในเซ็ตประกอบด้วย"
    set_contents_header = soup.find("div", class_="description-attribute-header", string="ในเซ็ตประกอบด้วย")

    if set_contents_header:
        # Try to find the next div after the header containing the set contents
        set_contents_div = set_contents_header.find_next("div")
        
        if set_contents_div:
            # Split the content by line breaks or '*' for items that might be in bullet point form
            product_details["set_contents"] = [item.strip() for item in set_contents_div.text.split("*") if item.strip()]
        else:
            product_details["set_contents"] = None
    else:
        product_details["set_contents"] = None

    # Extract "ข้อมูลน่ารู้" or other important facts
    facts_header = soup.find("div", class_="description-attribute-header", string="ข้อมูลน่ารู้")

    if facts_header:
        # Try to find the next div containing the information after the header
        facts_div = facts_header.find_next("div")

        if facts_div:
            # Grab the text from the facts div
            product_details["facts"] = facts_div.text.strip()
        else:
            product_details["facts"] = None
    else:
        product_details["facts"] = None

    driver.quit()

    return product_details

# Function to scrape products from the main listing page and get their details
def scrape_products(url):
    driver = webdriver.Chrome(options=chrome_options)
    driver.get(url)

    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CLASS_NAME, 'products-grid'))
        )
    except Exception as e:
        driver.quit()
        print(f"Error loading page: {e}")
        return []

    page_source = driver.page_source
    soup = BeautifulSoup(page_source, 'html.parser')
    product_cards = soup.find_all('div', class_='product-item')

    products = []
    for product in product_cards:
        brand = product.find(
            'p', class_='product-card-brand').get_text(strip=True)
        product_name = product.find(
            'p', class_='product-card-product').get_text(strip=True)
        old_price = product.find('span', class_='product-price-sale-old').get_text(
            strip=True) if product.find('span', class_='product-price-sale-old') else None
        new_price = product.find('span', class_='product-price-sale-new').get_text(
            strip=True) if product.find('span', class_='product-price-sale-new') else None
        discount_text = product.find('span', class_='product-price-sale-text').get_text(
            strip=True) if product.find('span', class_='product-price-sale-text') else None

        if discount_text and '(' in discount_text and ')' in discount_text:
            discount_value = discount_text.split('(')[1].split(')')[0]
            discount_text = discount_value.strip()

        variants_count = product.find('div', class_='product-card-variants-count').get_text(
            strip=True) if product.find('div', class_='product-card-variants-count') else None
        rating_element = product.find('div', class_='rating-container')
        rating = rating_element.find('div', class_='rateit-range')[
            'aria-valuenow'] if rating_element and rating_element.find('div', class_='rateit-range') else None

        product_url = product.find(
            'a', class_='product-card-image-container')['href']
        full_link = f"https://www.sephora.co.th{product_url}"
        product_details = scrape_product_details(full_link)

        product_data = {
            'Brand': brand,
            'title': product_name,
            'Old Price': old_price,
            'New Price': new_price,
            'Discount': discount_text,
            'Variants': variants_count,
            'Rating': rating,
            'full_link': full_link,
            **product_details
        }

        products.append(product_data)

    driver.quit()
    return products

# Function to update products in Neo4j
def update_products_in_neo4j(products):
    existing_products = set()

    for product in products:
        title = product['title']
        existing_products.add(title)

        # Query to insert or update product details in Neo4j
        insert_query = '''
        MERGE (p:Product {title: $title})
        SET p.brand = $brand,
            p.old_price = $old_price,
            p.new_price = $new_price,
            p.discount = $discount,
            p.variants = $variants,
            p.rating = $rating,
            p.description = $description,
            p.benefits = $benefits,
            p.benefits_list = $benefits_list,
            p.product_claims = $product_claims,
            p.ingredients = $ingredients,
            p.how_to_use = $how_to_use,
            p.set_contents = $set_contents,
            p.facts = $facts,
            p.full_link = $full_link,
            p.image_url = $image_url
        '''

        run_query(insert_query, {
            'title': title,
            'brand': product.get('Brand'),
            'old_price': product.get('Old Price'),
            'new_price': product.get('New Price'),
            'discount': product.get('Discount'),
            'variants': product.get('Variants'),
            'rating': product.get('Rating'),
            'description': product.get('product_description'),
            'benefits': product.get('benefits'),
            'benefits_list': "; ".join(product.get('benefits_list', [])),  # Convert list to a string
            'product_claims': product.get('product_claims'),
            'ingredients': product.get('ingredients'),
            'how_to_use': product.get('how_to_use'),
            'set_contents': product.get('set_contents'),
            'facts': product.get('facts'),
            'full_link': product.get('full_link'),
            'image_url': product.get('image_url')
        })
        print(
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Added/Updated product: {title}")

    # Delete products that are no longer available
    delete_query = '''
    MATCH (p:Product)
    WHERE NOT p.title IN $titles
    DETACH DELETE p
    '''
    run_query(delete_query, {'titles': list(existing_products)})
    print(
        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Deleted Products that are no longer available.")

    delete_brands_query = '''
    MATCH (b:Brand)
    WHERE NOT (b)-[:SELLS]->(:Product)
    DETACH DELETE b
    '''
    run_query(delete_brands_query)
    print(
        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Deleted Brands that are no longer available.")

# API route to get products
@app.route('/api/products', methods=['GET'])
def get_products():
    page = 1
    all_products = []

    while True:
        url = f"https://www.sephora.co.th/sale?page={page}"
        print(f"Scraping page: {url}")
        products_on_page = scrape_products(url)

        if not products_on_page:
            break

        print(f"Found {len(products_on_page)} products on page {page}")
        all_products.extend(products_on_page)
        page += 1
        time.sleep(2)

    print(f"Total products collected: {len(all_products)}")

    # Update products in Neo4j
    update_products_in_neo4j(all_products)

    return jsonify(all_products)

# Start the Flask server
if __name__ == '__main__':
    app.run(port=7021)

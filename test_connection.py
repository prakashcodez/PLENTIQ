import mysql.connector
from getpass import getpass

try:
    password = getpass("Enter your MySQL password: ")

    connection = mysql.connector.connect(
        host="localhost",
        user="root",
        password=password
    )

    cursor = connection.cursor()

    cursor.execute("SELECT VERSION()")

    version = cursor.fetchone()[0]

    print("Connected to MySQL successfully!")
    print("MySQL version:", version)

    cursor.close()
    connection.close()

except mysql.connector.Error as error:
    print("Connection failed:", error)
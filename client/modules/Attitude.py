# -*- coding: utf-8-*- 
import random
import re

WORDS = ["ATTITUDE", "PROBLEM"]

def handle(text, mic, profile): 
	"""
		Responds to inquiries about its bad ATTITUDE

	"""
	messages = ["I don't have a problem, punk.", 
				"You need to check yourself before you wreck yourself"]

	message = random.choice(messages)

	mic.say(messages)

def isValid(text): 
	"""
		Returns True if the input is related to a supposed bad attitude
	"""
	return bool(re.search(r'\bbad attitude\b', text, re.IGNORECASE))
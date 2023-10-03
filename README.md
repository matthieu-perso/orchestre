# Orchestre
Automate any third party integration with AI. 

Orchestre listens for specific events on the third party app, then responds with AI. These response can be customized - Orchestre can query any information (products, data etc...) from databases and generate tailored responses. 

With its extensible architecture, you can easily integrate your own products, manage users, and deploy to production fast.

**Use case** : Let's say you want to automate customer service response for 15 Gmail accounts. Connect the accounts to Orchestre, add your LLM, customize your prompts, and Orchestre automates the responses for you. 


Key Features:
- Connect prebuilt third party extensions (Gmail etc...)
- Easily connect different types of LLMs (OpenAI, LLama, Huggingface etc..) 
- Integrate external information & products in your workflow (Connect your firebase database) 
- User management API. 
- Easy deployment with Docker
- Easily integrate with Langchain and LlamaIndex

Orchestre is built for scale. You can 
- Have multiple users on one server
- Have multiple providers (eg third party apps) in one account.

Imagine - you're able to automate all your company's emails from one orchestre server. Simple to monitor and to manage.  

Contributions are welcome! Also feel free to reach out to me if the project is of interest.

# 🚀 Quickstart 

There are two simple steps 
- Set up the server 
- Set up your integrations 

## Server & LLMs

You have to add your environement variables. Rename the `.env.sample`  file to `.env` and add in the relevant variables. 

- Add a `SESSION_KEY` : Can be anything, but this allows you to keep your account secure. 
- Download your service account and rename to `fireabase-serviceaccount.json` 
- Download your firebase config and rename to ` firebase.json` 
- Add your LLM keys (eg add in your OpenAI api key)


## Services 

For the Gmail service 
- Create a Oauth Credential from your GCP account. More info [here](https://developers.google.com/identity/protocols/oauth2)
- Download the credential file and rename to `oauth2_credentials.json` 

## Run the app locally

Install [poetry](https://python-poetry.org/) and run install the requirements with the command below :  

```bash
poetry install
```

- Run uvicorn server

```bash
poetry run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

- Check Swagger documentation page

You're set ! Have a look at the endpoints at :

```
http://localhost:8000/docs
```

Now that the server is live - setup a user using the signup endpoint & connect your providers and LLMs !

# Documentation

For more information, check out our documentation. It contains : 
- Getting started (installation, setting up the environment, simple examples)
- How-To examples
- Reference (full API docs)
- Resources (high-level explanation of core concepts)
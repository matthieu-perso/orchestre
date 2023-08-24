# Orchestre
Automate any third party integration with AI. 

Orchestre provides a seamless way to automate third-party integrations using AI and LLM services. With its extensible architecture, you can easily integrate your own products, manage users, and deploy.

Orchestre listens for specific events on the third party app, then responds with AI. These response can be customized - Orchestre can query any information (products, data etc...) from databases and generate tailored responses. 


Key Features:
- Connect prebuilt third party extensions (Gmail etc...)
- Easily connect different types of LLMs  
- Integrate external information & products in your workflow. 
- User management API. 
- Easy deployment with Docker


Contributions are welcome!


# 1. Quickstart 

## 1.1 Knowledge

- Environment variables

The .env.sample file contains the sample format for .env file.

- Firebase and OAuth2-crendential

This project uses Firebase authentication for user management and GMail APIs. so you have to configure your firebase project and google cloud configuration.

```
firebase-serviceaccount.json : Firebase Service account
firebase.json : Firebase configuration
oauth2-credentials.json : Google credentials
```

- config.py

This class reads the environment variables and use it for appropriate AI model classes

## 1.1 Configure development environment

- install python 3.10 & poetry, uvicorn on your PC

This project uses Python 3.10, you can add dependencies by using following commands

```
python -m venv .venv
source .venv\Script\activate
pip install poetry
pip install uvicorn
poetry install
```

- copy .env.sample to .env file
- Add your LLM API keys in the .env file in the root directory.
- Configure your Firebase project and oauth2-credential.json from google platform

## 1.2 Test project

- Run uvicorn server

```
poetry run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

- Check Swagger documentation page

After launching the uvicorn server, please view it on : 

```
http://localhost:8000/docs
```


# 2. Directory Structure

- apis: This directory contains the api endpoints declarations (user, bot, message, provider features)
- core: This directory contains the core classes; bot, dynamic loader, task management, utils classes
- db: This directory contains the Firebase database access functions
- helpers: This directory contains the helper classes, at this moment, only one form class
- products: This directory contains the Product associated classes
- providers: This directory contains the Provider classes. New provider classes will be added here
- services: This directory contains the class for LLMs.
- tests : This directory contains the unit-test classes

# 3. New custom provider

When you are going to add new custom provider class, you can add new provider class under /providers/plugins/{new provider} directory.
The new custom provider has to inherit BaseProvider class and implement the appropriate functions. 
You can check dummy or gmail providers

```
class GMailProvider(BaseProvider):
    def __init__(self):
        self.sync_time = -1
        self.access_token = None
        self.refresh_token = None

    def get_provider_info(self):
        return {
            "provider": GMailProvider.__name__.lower(),
            "short_name": "Gmail",
            "provider_description": "GMail Provider",
            "provider_icon_url": "/gmail.svg",
        }
   ....
```

You can get more additional information in /docs directory.

# 4. LLM AI Services

In services/llm/services.py, There are LLM service classes for OpenAI, HuggingFace and etc...
If you want to add new AI models, you can add here.

   
# 5. API Endpoints

## 5.1 Authentication and Test account

This project is based on Firebase email/password authentication.

- Test user :
test@gmail.com / testtest

- Authorize :
To check the user authenticated protected endpoints, you first click 'Authorize' button and then input test user credentials

## 5.2 User management

- /users/signup : 
This endpoint will create user account on Firebase

- /users/token : 
This endpoint is for user login process

- /users/loginWithToken : 
This endpoint is used for 3rd party token based authentication. This allows GMail authentication at this moment. 

- /users/me : 
This endpoint is for getting user information

## 5.3 Provider management

After user authentication, the user can link their social accounts for chatbot such as gmail, linkedin and etc.
Here we called those as 'provider'

- /providers/google_auth : 
This endpoint is the redirect_url which can be registered in google cloud for google user authentication
***For other providers, there will be more endpoints for redirect_url in future

- /providers/link_provider : 
This endpoint attach the user's social accounts to him.
For example, if we call this function with "gmailprovider" parameter, it will ask gmail authentication.
After done, the user will get the access_token and then use it for chatbot feature later
***In swagger documentation, this endpoint not working, because it will go to the redirect_url on the page.
before check this, you have to register redirect_url on your google cloud platform.
so to check this api, you can input the following link in other chrome tab.
http://localhost:8000/providers/link_social_provider?provider_name=gmailprovider&redirect_url=http://localhost:3000/callback/auth
after call there, you have to authenticate your test gmail account and get the access_token on the screen
***In production mode, this endpoint has to be user authentication protected, but now it is free for checking

- /providers/update_provider_info : 
This endpoint updates the user's provider information
In above endpoint, after we authenticate the gmail account, and get the access_token and extra.
You can save these information via this endpoint.
In above endpoint, you can get the JSON format response, and then extract 'data' field from it, and then input it as 'social_info' field
The following is the sample
{
    "access_token": "ya29.a0AWY7CkmkohVymfu7QM6SOrfC8M37dM93tyt8y........Q0163",
    "expires_in": 3599,
    .......
    "iat": 1685200150,
    "exp": 1685203750,
    "userinfo": {.....}
}

This information will be used for auto-chatbot feature

- /providers/get_my_providers : 
This endpoint returns all the provider instances which associated with the login end-user

- /providers/get_providers : 
This endpoint returns the all the provider instances registered in the system

- /providers/unlink_provider : 
This endpoint detach the user's social account from him.


## 5.4 Message management

This features are used internally by bot class. but have exported these endpoints for simple testing.

- /messages/get_last_message : 
This endpoint will get the latest message from provider.
For example, if we choose provider_name as 'gmailprovider', it will get the last message from someone in gmail account.
Here we have to use access_token from which we get the above /providers/link_provider endpoint

- /messages/get_messages : 
This endpoint will get the messages from sepcific time 
here from_when parameter is the unix timestamp for specific time.
For example, if we choose provider_name as 'gmailprovider', and then specify the timestamp for from_when

- /providers/reply_to_message : 
This endpoint will reply to specific message via provider
Here 'to' parameter will indicates the message for various providers
For example, if we choose provider_name as 'gmailprovider', 'to' parameter will be the 'messageId' of gmail.
In other social platform, it can be varied by following their definitions


## 5.5 Bot management


- /bots/start_auto_bot : 
This function starts the auto-bot task for specific provider instance of logged user
here 'inteval_seconds' means the time interval

- /bots/stop_auto_bot : 
This function will stop auto-bot task for specific provider instance of logged user

- /bots/status_auto_bot : 
this function will return the auto-bot status of logged user



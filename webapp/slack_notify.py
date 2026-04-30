import os
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import ssl

client = WebClient(token='your-slack-bot-token')


def build_option(text, value):
    option = {}
    option['text'] = {}
    option['text']["type"] = 'plain_text'
    option['text']["text"] = text
    # option['text']['emoji']=True

    option['value'] = f"{value}"
    return option


def build_txt_input(id, text):
    section = {
        "type": "input",
        "element": {"type": "plain_text_input", "action_id": id},
        "label": {
            "type": "plain_text",
            "text": text,
        },
    }
    return section


def build_button(action_id, value):
    section = {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "提交"},
                "value": value,
                "action_id": action_id,
            }
        ],
    }
    return section


def build_select_section(
    options, action_id, text="Pick an item from the dropdown list", type="static_select"
):
    section = {
        "type": "section",
        "text": {"type": "mrkdwn", "text": text},
        "accessory": {
            "type": type,
            "placeholder": {
                "type": "plain_text",
                "text": "选择一个",
                # "emoji": True
            },
            "options": options,
            "action_id": action_id,
        },
    }
    return section


def built_divider():
    divider = {"type": "divider"}
    return divider


def built_section(text, img_url=None, img_text=None):
    if img_url:
        section = {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f'*{img_text}*\n{text}'},
            "accessory": {"type": "image", "image_url": img_url, "alt_text": img_text},
        }
    else:
        section = {
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        }
    return section


def get_chanal_id(name):
    global client
    chanal_id = ''
    response = client.conversations_list()
    conversations = response["channels"]
    for chanal_information in conversations:
        # print(chanal_information)
        if chanal_information['name'] == name:
            chanal_id = chanal_information['id']
            break
    return chanal_id


def send_message(message, channel_name='下载器交互'):
    channel_id = get_chanal_id(channel_name)
    try:
        response = client.chat_postMessage(channel=channel_id, text=message)

        return response.data['ts']
    except SlackApiError as e:
        # You will get a SlackApiError if "ok" is False
        print(f'error is {e}')


def send_file(channel_name, file, title, comment):
    channel_id = get_chanal_id(channel_name)

    response = client.files_upload_v2(
        file=file,
        title=title,
        # Note that channels still works but going with channel="C12345" is recommended
        # channels=["C111", "C222"] is no longer supported. In this case, an exception can be thrown
        channels=channel_id,
        initial_comment="Here is the latest version of our new company logo :wave:",
    )
    response.get("file")
    return response.data['ts']


def send_files(files, channel_name='下载器交互', initial_comment='test'):
    file_uploads = []
    for file in files:

        file_uploads.append(file)
    channel_id = get_chanal_id(channel_name)
    try:
        response = client.files_upload_v2(
            file_uploads=file_uploads,
            channel=channel_id,
            initial_comment=initial_comment,
        )
    except Exception as e:
        print(f'e is {e}')
        print('send_files——ssl连接失败')
    return ""


def send_message_block(
    blocks, Label='选择正确的剧集或者电影', channel_name='下载器交互'
):
    channel_id = get_chanal_id(channel_name)
    response = client.chat_postMessage(
        channel=channel_id,
        blocks=blocks,
    )
    return response.data['ts']


def push_tmdb(**kwargs):
    pass


if __name__ == '__main__':
    # send_message('学校的vertex','test')
    message_id = send_message(message='test')
    print(message_id)
    pass

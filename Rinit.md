I want to build a web application with node.js as front-end and python as back end service, using milvus (milvus-standalone:19530/default(no need user and
  password ))and postgresql(psql -p5432 -U "jianbo" "jianbo" - local connection method) in local， also can use Gemini as backend LLM to solve the question and give the students good answers including some visualization result. Here is some main requirements:

1.   User can put an image ( 1 image for 1 question in current stage) to the application, and then can get the details answer. 
1.1 The application can parse the image and understand the question in the image based on the back end LLM.
1.2 The understanding of the question can find the key points not just for answering the question, but also can find key points for a students (middle school students and high school  students) to understand  the answer (including some key points of the question, key points of the answer, if exist some pattern that students should focus on, some methods the students can solve the same kind of questions). 
1.3 This application is not just providing answers for questions, but more focusing on how to let the students learn the same kind of questions and find a way to solve similar questions.
1.4 The answer  should not just focusing on text mode answer, better with some visualization parts to help the students to understand  the questions and the answers. The visualization contents is also generated  by the backend llm, but can be rendered with good visualization results using JSXGraph as frontend render engine lithe some animation results like some points can run as circle based on the questions answer.  - This part need to think how to design a good application results with llm, not just let llm output some js but with some good design for it.
1.5 The answer also should include some similar questions with answers that can help the students to practice to make them really master the method.
1.6 The llm can extract key points to stored into the backend vector db (miles) and postgresql for future  query. This part also need to be carefully design to sediment or accumulate  knowledge  and key points of methods then easy to query for future. 
1.6 The user can query similar questions  and find related answers  that help user to understand his status.
1.7 select several questions  can generate new questions as a exam with answers  for practice.

2. Technical design :
2.1 using node.js as front-end ,python as back-end with api services. Can use JSXGraph as good math visualization rendering library for good understanding of knowledge.
2.2 using gemini as llm for generated related answer and find good way to render with good visualization  results.
2.3  using milvus (milvus-standalone:19530/default(no need user and  password )) to store vector contents and postgresql(psql -p5432 -U "jianbo" "jianbo" - local connection method) to store structured contents.
2.4 The interaction should be friendly and good for using ,the answer  should be good for students to understand, the user experience  should be good for users.